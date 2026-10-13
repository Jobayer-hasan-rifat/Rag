"""Answer-generation evaluation: real retrieval, a real language model, deterministic metrics.

Run from `backend/` with Docker (a throwaway PostgreSQL) and a running provider, for example a
local Ollama server that already has the model:

    python -m evaluation.rag_eval --model qwen2.5:latest --json evaluation/results/rag_qwen2.5.json

The Phase 7 corpora plus `evaluation/rag/corpus.json` (contradictions, documents with injected
instructions, a very long passage, identifiers) are loaded as one user's documents and embedded with
the real embedding model. Every question goes through the real `RagService`. Metrics are
deterministic (see rag_metrics.py): fact recall against author-written expected facts, abstention,
citation validity and correctness, forbidden strings, latency and token counts. There is no
model-as-judge. Each answer is stored in the JSON file so a person can read it: the faithfulness
review in the documentation is a single-reviewer author judgement of those stored answers.
"""

import argparse
import asyncio
import contextlib
import json
import platform
import subprocess
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.db.repositories.collection_repository import CollectionRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.embedding_repository import EmbeddingRepository
from app.db.repositories.lexical_repository import LexicalRepository
from app.db.session import create_engine
from app.embeddings.registry import get_provider
from app.exceptions import AppError
from app.models.user import User
from app.rag.tokens import bengali_share
from app.services.hybrid_search_service import HybridSearchService
from app.services.rag_service import RagService
from evaluation.dataset import RagQuestion, load_rag_corpus, load_rag_questions
from evaluation.rag_metrics import (
    QuestionResult,
    contains_forbidden,
    fact_complete,
    fold,
    group_by,
    summarise,
)
from evaluation.retrieval_eval import _database, _settings_for, load_and_embed
from tests.helpers import create_user_in_db


def _language(question: str) -> str:
    share = bengali_share(question)
    return "bn" if share >= 0.7 else ("en" if share < 0.1 else "mixed")


def _stop_model(model: str) -> None:
    """Unload the model so the first request measures a cold start (Ollama only)."""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["ollama", "stop", model], capture_output=True, timeout=30, check=False)


def _ollama_ps() -> str | None:
    try:
        result = subprocess.run(
            ["ollama", "ps"], capture_output=True, timeout=30, check=False, text=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


async def run(
    settings: Settings, questions: list[RagQuestion], user_id: uuid.UUID, retrieval: str
) -> tuple[list[QuestionResult], dict[str, Any]]:
    corpus = load_rag_corpus()
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider = get_provider(settings)
    provider.warmup()
    try:
        chunk_to_passage, embed_seconds, failures = await load_and_embed(
            settings, provider, factory, corpus, user_id
        )
        passage_text = {p.id: p for p in corpus.passages}
        results: list[QuestionResult] = []
        async with factory() as session:
            user = await session.get(User, user_id)
            assert user is not None
            service = RagService(
                hybrid=HybridSearchService(
                    session=session, documents=DocumentRepository(session),
                    collections=CollectionRepository(session),
                    embeddings=EmbeddingRepository(session), lexical=LexicalRepository(session),
                    provider=provider, gate=asyncio.Semaphore(1), settings=settings,
                ),
                rerank=None, gate=asyncio.Semaphore(1), settings=settings,
            )  # fmt: skip
            for question in questions:
                await session.refresh(user)
                result = QuestionResult(
                    id=question.id, category=question.category,
                    language=_language(question.question), abstain_expected=question.abstain,
                    answered=False, insufficient_reason=None, answer="",
                    relevant=list(question.relevant),
                )  # fmt: skip
                started = time.perf_counter()
                try:
                    outcome = await service.answer(
                        user, question=question.question, document_ids=None, collection_id=None,
                        retrieval=retrieval, rerank=False, max_sources=None,
                        max_answer_tokens=None,
                    )  # fmt: skip
                except AppError as error:
                    result.error = error.code + ":" + str(error.details.get("reason", ""))
                    result.latency_ms = round((time.perf_counter() - started) * 1000)
                    results.append(result)
                    continue
                result.latency_ms = round((time.perf_counter() - started) * 1000)
                result.answered = not outcome.insufficient
                result.insufficient_reason = outcome.insufficient_reason
                result.answer = outcome.answer
                result.candidate_passages = [
                    chunk_to_passage[r.record.chunk_id] for r in outcome.retrieval.results
                ]
                result.source_passages = [
                    chunk_to_passage[b.record.chunk_id] for b in outcome.context.blocks
                ]
                result.cited_passages = [
                    chunk_to_passage[c.block.record.chunk_id] for c in outcome.citations
                ]
                labels = {b.label for b in outcome.context.blocks}
                result.labels_valid = all(
                    c.block.label in labels and f"[{c.block.label}]" in outcome.answer
                    for c in outcome.citations
                )
                for citation in outcome.citations:
                    passage = passage_text[chunk_to_passage[citation.block.record.chunk_id]]
                    record = citation.block.record
                    result.citation_metadata_ok &= (
                        record.document_name == f"{passage.document_id}.md"
                        and record.page_number is None
                        and fold(citation.excerpt.rstrip("…")) in fold(passage.text)
                    )
                if outcome.grounded is not None:
                    g = outcome.grounded
                    result.removed = (
                        g.removed_unknown_labels + g.removed_uncited_sentences
                        + g.removed_fabricated_quotes + g.removed_unsupported_literals
                    )  # fmt: skip
                if outcome.generation is not None:
                    usage = outcome.generation.result
                    result.input_tokens, result.output_tokens = (
                        usage.input_tokens,
                        usage.output_tokens,
                    )
                    result.attempts = outcome.generation.attempts
                result.generation_ms = outcome.timings["generation_ms"]
                result.retrieval_ms = outcome.timings["retrieval_ms"]
                result.context_ms = outcome.timings["context_ms"]
                if not question.abstain:
                    result.fact_complete = fact_complete(outcome.answer, question.expected)
                result.forbidden_hit = contains_forbidden(outcome.answer, question.forbidden)
                results.append(result)
        meta = {"embed_seconds": round(embed_seconds, 2), "embedding_failures": failures}
        return results, meta
    finally:
        await engine.dispose()


def print_report(report: dict[str, Any]) -> None:
    print(
        f"model={report['model']} provider={report['provider']} questions={report['questions']} "
        f"retrieval={report['retrieval']}"
    )
    for key, value in report["overall"].items():
        print(f"  {key}: {value}")
    for title in ("by_category", "by_language"):
        print(f"\n### {title}")
        for name, entry in report[title].items():
            print(
                f"- {name} ({entry['questions']}): "
                f"fact_recall={entry['fact_recall_on_answerable']} "
                f"abstain_acc={entry['abstention_accuracy_on_unanswerable']} "
                f"gold_cite={entry['answers_with_a_gold_citation']} "
                f"forbidden={entry['forbidden_string_rate']} "
                f"latency={entry.get('latency_ms')}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--retrieval", choices=["hybrid", "semantic"], default="hybrid")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--cold-start", action="store_true", help="unload the model first (Ollama)")
    parser.add_argument("--max-chunk-tokens", type=int)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    import torch

    print(
        f"Python {platform.python_version()} on {platform.platform()} ({platform.processor()}), "
        f"torch {torch.__version__}, {torch.get_num_threads()} threads",
        flush=True,
    )
    questions = load_rag_questions()[: args.limit]
    url, cleanup = _database()
    try:
        owner = uuid.UUID(create_user_in_db(url, email="eval@example.com"))
        update: dict[str, Any] = {
            "rag_enabled": True, "llm_provider": args.provider, "llm_base_url": args.base_url,
            "llm_model": args.model, "search_max_top_k": 50,
        }  # fmt: skip
        if args.max_chunk_tokens:
            update["rag_max_chunk_tokens"] = args.max_chunk_tokens
        settings = _settings_for(url, None).model_copy(update=update)
        if args.cold_start and args.provider == "ollama":
            _stop_model(args.model)
        results, meta = asyncio.run(run(settings, questions, owner, args.retrieval))
        processes = _ollama_ps() if args.provider == "ollama" else None
    finally:
        cleanup()
    report: dict[str, Any] = {
        "dataset": "rag-answer-eval-synthetic v1 over the Phase 7 corpora plus rag extra corpus",
        "model": args.model, "provider": args.provider, "retrieval": args.retrieval,
        "questions": len(results),
        "settings": {
            "temperature": settings.llm_temperature,
            "max_output_tokens": settings.llm_max_output_tokens,
            "context_tokens": settings.llm_context_tokens,
            "rag_candidates": settings.rag_candidates,
            "rag_max_sources": settings.rag_max_sources,
            "rag_max_chunk_tokens": settings.rag_max_chunk_tokens,
            "rag_context_token_budget": settings.rag_context_token_budget,
            "structured_output": settings.llm_structured_output,
        },
        "retrieval_meta": meta, "cold_start_requested": args.cold_start,
        "first_request_ms": results[0].latency_ms if results else None,
        "runtime": processes,
        "overall": summarise(results),
        "by_category": group_by(results, "category"),
        "by_language": group_by(results, "language"),
        "results": [asdict(r) for r in results],
    }  # fmt: skip
    print_report(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
