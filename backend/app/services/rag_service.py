import asyncio
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.config import Settings
from app.exceptions import (
    GenerationFailedError,
    GenerationUnavailableError,
    RequestValidationFailedError,
    RerankingDisabledError,
)
from app.llm.registry import get_llm_provider
from app.llm.types import (
    GenerationRequest,
    GenerationResult,
    LLMBadResponseError,
    LLMError,
    LLMProvider,
    LLMRateLimitedError,
    LLMUnavailableError,
)
from app.models.user import User
from app.observability.logging import get_logger
from app.rag.context import ContextBlock, ContextBuilder, ContextSelection
from app.rag.grounding import (
    GroundedAnswer,
    InvalidModelOutputError,
    excerpt,
    ground_answer,
    insufficient_message,
    parse_model_output,
)
from app.rag.history import (
    HistorySelection,
    HistoryTurn,
    render_history,
    retrieval_query,
    select_history,
)
from app.rag.prompt import (
    ANSWER_SCHEMA,
    PROMPT_VERSION,
    PROMPT_VERSION_HISTORY,
    build_user_message,
    load_system_prompt,
)
from app.rag.tokens import estimate_tokens
from app.reranking.ranking import RerankInput
from app.services.hybrid_search_service import HybridOutcome, HybridSearchService, Retriever
from app.services.rerank_service import RerankService
from app.services.search_common import prepare_query

logger = get_logger("app.rag")

MAX_ATTEMPTS = 2  # one automatic retry when the completion is not a usable answer object
MIN_CONTEXT_TOKENS = 200


@dataclass(frozen=True)
class Citation:
    block: ContextBlock
    excerpt: str
    excerpt_truncated: bool


@dataclass(frozen=True)
class GenerationDetails:
    provider: str
    model: str
    prompt_version: str
    attempts: int
    result: GenerationResult


@dataclass
class RagOutcome:
    answer: str
    insufficient: bool
    insufficient_reason: str | None
    citations: list[Citation]
    mode: str
    retrieval: HybridOutcome
    degraded: list[str]
    reranked: bool
    context: ContextSelection
    generation: GenerationDetails | None
    grounded: GroundedAnswer | None
    timings: dict[str, int] = field(default_factory=dict)
    history: HistorySelection | None = None  # turns shown to the model (conversations only)
    retrieval_extended: bool = False  # the retrieval query included an earlier question


class RagService:
    """Question, authorised retrieval, optional reranking, context, generation, validation.

    Retrieval and reranking are the existing services, so ownership and `ready`-only filtering
    happen before any text exists in this pipeline. The database connection is released before
    the model is called, and the model only ever sees what `ContextBuilder` selected.
    """

    def __init__(
        self,
        *,
        hybrid: HybridSearchService,
        rerank: RerankService | None,
        gate: asyncio.Semaphore,
        settings: Settings,
    ) -> None:
        self._hybrid = hybrid
        self._rerank = rerank
        self._gate = gate
        self._settings = settings

    def _limits(self, max_sources: int | None, max_answer_tokens: int | None) -> tuple[int, int]:
        settings = self._settings
        sources = max_sources or settings.rag_max_sources
        tokens = max_answer_tokens or settings.llm_max_output_tokens
        if sources > settings.rag_max_sources:
            raise RequestValidationFailedError(
                f"max_sources must be at most {settings.rag_max_sources}"
            )
        if tokens > settings.llm_max_output_tokens:
            raise RequestValidationFailedError(
                f"max_answer_tokens must be at most {settings.llm_max_output_tokens}"
            )
        return sources, tokens

    def validate(
        self,
        question: str,
        *,
        max_sources: int | None,
        max_answer_tokens: int | None,
        rerank: bool,
    ) -> str:
        """The cheap request checks of `answer`, without touching the database or the model.
        Returns the normalised question. Raises the same errors `answer` would."""
        text = prepare_query(question, self._settings)
        self._limits(max_sources, max_answer_tokens)
        if rerank and self._rerank is None:
            raise RerankingDisabledError()
        return text

    async def answer(
        self,
        actor: User,
        *,
        question: str,
        document_ids: list[uuid.UUID] | None,
        collection_id: uuid.UUID | None,
        retrieval: str | None,
        rerank: bool,
        max_sources: int | None,
        max_answer_tokens: int | None,
        history: Sequence[HistoryTurn] = (),
    ) -> RagOutcome:
        started = time.monotonic()
        settings = self._settings
        text = prepare_query(question, settings)
        sources_limit, answer_tokens = self._limits(max_sources, max_answer_tokens)
        mode = retrieval or settings.rag_retrieval_mode
        if rerank and self._rerank is None:
            raise RerankingDisabledError()
        prompt_version = PROMPT_VERSION_HISTORY if history else PROMPT_VERSION
        system_prompt = load_system_prompt(prompt_version)
        selection_of_history: HistorySelection | None = None
        history_block: str | None = None
        retrieval_text, extended = text, False
        if history:
            selection_of_history = select_history(
                history,
                max_turns=settings.conversation_history_turns,
                budget_tokens=settings.conversation_history_token_budget,
                message_tokens=settings.conversation_history_message_tokens,
            )
            if selection_of_history.turns:
                history_block = render_history(selection_of_history.turns)
            retrieval_text, extended = retrieval_query(
                text,
                [turn.question for turn in history],
                max_words=settings.conversation_followup_max_words,
                turns=settings.conversation_retrieval_context_turns,
                max_chars=settings.search_max_query_chars,
            )
        history_tokens = estimate_tokens(history_block) if history_block else 0
        budget = min(
            settings.rag_context_token_budget,
            settings.llm_context_tokens
            - answer_tokens
            - estimate_tokens(system_prompt)
            - estimate_tokens(text)
            - history_tokens
            - 300,
        )
        if budget < MIN_CONTEXT_TOKENS:
            raise RequestValidationFailedError("The question is too long for the answer budget")

        retrievers: frozenset[Retriever] = (
            frozenset({"dense", "sparse"}) if mode == "hybrid" else frozenset({"dense"})
        )
        outcome = await self._hybrid.search(
            actor,
            query=retrieval_text,
            top_k=None,
            document_ids=document_ids,
            collection_id=collection_id,
            retrievers=retrievers,
            candidate_limit=settings.rag_candidates,
        )
        await self._hybrid.release_connection()  # no connection is held from here on
        retrieval_ms = round((time.monotonic() - started) * 1000)

        ranked = [(item.record, item.rank) for item in outcome.results]
        degraded = list(outcome.diagnostics.degraded)
        reranked = False
        rerank_ms = 0
        if rerank and ranked and self._rerank is not None:
            rerank_started = time.monotonic()
            result = await self._rerank.rerank(
                retrieval_text,
                [RerankInput(r.chunk_id, r.text, rank) for r, rank in ranked],
                top_k=len(ranked),
            )
            rerank_ms = round((time.monotonic() - rerank_started) * 1000)
            if result.applied:
                by_id = {record.chunk_id: (record, rank) for record, rank in ranked}
                ranked = [by_id[item.key] for item in result.items]
                reranked = True
            else:
                degraded.append("rerank")

        context_started = time.monotonic()
        selection = ContextBuilder(
            max_sources=sources_limit,
            max_chunk_tokens=settings.rag_max_chunk_tokens,
            budget_tokens=budget,
        ).build(ranked)
        context_ms = round((time.monotonic() - context_started) * 1000)

        generation: GenerationDetails | None = None
        grounded: GroundedAnswer | None = None
        generation_ms = 0
        if not selection.blocks:
            reason: str | None = "retrieval_degraded" if degraded else "no_sources"
        else:
            generation_started = time.monotonic()
            generation, grounded = await self._generate(
                text, selection.blocks, system_prompt, answer_tokens, prompt_version, history_block
            )
            generation_ms = round((time.monotonic() - generation_started) * 1000)
            reason = None if grounded.answerable else grounded.reason

        by_label = {block.label: block for block in selection.blocks}
        citations = []
        if grounded is not None and grounded.answerable:
            for label in grounded.labels:
                shown, cut = excerpt(by_label[label].text)
                citations.append(Citation(by_label[label], shown, cut))
        answer = (
            grounded.text
            if grounded and grounded.answerable
            else insufficient_message(text, search_degraded=reason == "retrieval_degraded")
        )
        timings = {
            "retrieval_ms": retrieval_ms,
            "rerank_ms": rerank_ms,
            "context_ms": context_ms,
            "generation_ms": generation_ms,
            "total_ms": round((time.monotonic() - started) * 1000),
        }
        self._log(mode, outcome, selection, generation, grounded, reason, timings, len(text))
        return RagOutcome(
            answer=answer,
            insufficient=reason is not None,
            insufficient_reason=reason,
            citations=citations,
            mode=mode,
            retrieval=outcome,
            degraded=degraded,
            reranked=reranked,
            context=selection,
            generation=generation,
            grounded=grounded,
            timings=timings,
            history=selection_of_history,
            retrieval_extended=extended,
        )

    async def _generate(
        self,
        question: str,
        blocks: list[ContextBlock],
        system_prompt: str,
        answer_tokens: int,
        prompt_version: str,
        history_block: str | None,
    ) -> tuple[GenerationDetails, GroundedAnswer]:
        settings = self._settings
        try:
            provider = get_llm_provider(settings)
        except LLMUnavailableError as error:
            logger.error("generation provider cannot be created", extra={"reason": error.reason})
            raise GenerationUnavailableError("unavailable") from None
        request = GenerationRequest(
            system=system_prompt,
            user=build_user_message(question, blocks, history_block),
            max_output_tokens=answer_tokens,
            json_schema=ANSWER_SCHEMA if settings.llm_structured_output != "none" else None,
        )
        try:
            await asyncio.wait_for(self._gate.acquire(), timeout=settings.rag_queue_timeout_seconds)
        except TimeoutError:
            logger.warning("generation is saturated")
            raise GenerationUnavailableError("busy") from None
        try:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                result = await self._call(provider, request)
                if (
                    result.input_tokens is not None
                    and result.input_tokens >= settings.llm_context_tokens
                ):
                    # the provider reports a prompt that filled the window: it may have cut the
                    # instructions, so nothing generated from it can be trusted
                    logger.error("prompt filled the context window")
                    raise GenerationFailedError()
                try:
                    parsed = parse_model_output(result.text)
                except InvalidModelOutputError:
                    logger.warning("completion was not a usable answer", extra={"attempt": attempt})
                    continue
                grounded = ground_answer(parsed, blocks)
                details = GenerationDetails(
                    settings.llm_provider, settings.llm_model, prompt_version, attempt, result
                )
                return details, grounded
        finally:
            self._gate.release()
        raise GenerationFailedError()

    @staticmethod
    async def _call(provider: LLMProvider, request: GenerationRequest) -> GenerationResult:
        try:
            return await provider.generate(request)
        except LLMRateLimitedError as error:
            raise GenerationUnavailableError(
                "rate_limited", retry_after=error.retry_after
            ) from None
        except LLMBadResponseError:
            raise GenerationFailedError() from None
        except LLMError as error:
            raise GenerationUnavailableError(error.reason) from None

    def _log(
        self,
        mode: str,
        outcome: HybridOutcome,
        selection: ContextSelection,
        generation: GenerationDetails | None,
        grounded: GroundedAnswer | None,
        reason: str | None,
        timings: dict[str, int],
        question_chars: int,
    ) -> None:
        logger.info(
            "rag answer",
            extra={
                "mode": mode,
                "question_chars": question_chars,
                "candidates": len(outcome.results),
                "sources": len(selection.blocks),
                "truncated_sources": selection.truncated_blocks,
                "answered": reason is None,
                "insufficient_reason": reason,
                "attempts": generation.attempts if generation else 0,
                "input_tokens": generation.result.input_tokens if generation else None,
                "output_tokens": generation.result.output_tokens if generation else None,
                "removed_labels": grounded.removed_unknown_labels if grounded else 0,
                "removed_uncited": grounded.removed_uncited_sentences if grounded else 0,
                "removed_quotes": grounded.removed_fabricated_quotes if grounded else 0,
                "removed_literals": grounded.removed_unsupported_literals if grounded else 0,
                **timings,
            },
        )
