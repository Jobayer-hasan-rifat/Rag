"""Turning a RAG outcome into the response models, shared by the answer and conversation
endpoints so a citation looks the same wherever it appears."""

from typing import Any

from app.schemas.rag import (
    CitationResponse,
    RagAnswerResponse,
    RagContextInfo,
    RagGenerationInfo,
    RagRetrievalInfo,
    RagTimings,
    RagValidationInfo,
)
from app.services.rag_service import Citation, RagOutcome


def build_citation(item: Citation) -> CitationResponse:
    record = item.block.record
    return CitationResponse(
        label=item.block.label,
        document_id=record.document_id,
        chunk_id=record.chunk_id,
        document_name=record.document_name,
        chunk_index=record.chunk_index,
        section_ordinal=record.section_ordinal,
        page_number=record.page_number,
        heading=record.heading,
        heading_path=list(record.heading_path),
        start_char=record.start_char,
        end_char=record.end_char,
        excerpt=item.excerpt,
        excerpt_truncated=item.excerpt_truncated,
        source_truncated=item.block.truncated,
    )


def build_response(outcome: RagOutcome) -> RagAnswerResponse:
    retrieval = outcome.retrieval
    diagnostics = retrieval.diagnostics
    grounded = outcome.grounded
    generation = None
    if outcome.generation is not None:
        result = outcome.generation.result
        generation = RagGenerationInfo(
            provider=outcome.generation.provider,
            model=outcome.generation.model,
            prompt_version=outcome.generation.prompt_version,
            attempts=outcome.generation.attempts,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            finish_reason=result.finish_reason,
            latency_ms=result.latency_ms,
        )
    return RagAnswerResponse(
        answer=outcome.answer,
        insufficient_evidence=outcome.insufficient,
        insufficient_reason=outcome.insufficient_reason,
        citations=[build_citation(item) for item in outcome.citations],
        retrieval=RagRetrievalInfo(
            mode=outcome.mode,
            candidates=len(retrieval.results),
            dense_candidates=diagnostics.dense_candidates,
            sparse_candidates=diagnostics.sparse_candidates,
            retrievers_used=list(diagnostics.retrievers_used),
            degraded=outcome.degraded,
            reranked=outcome.reranked,
            embedding_model=retrieval.config.model,
            model_revision=retrieval.config.revision,
        ),
        context=RagContextInfo(
            sources_supplied=len(outcome.context.blocks),
            duplicates_dropped=outcome.context.duplicates_dropped,
            truncated_sources=outcome.context.truncated_blocks,
            estimated_input_tokens=outcome.context.estimated_tokens,
            budget_tokens=outcome.context.budget_tokens,
        ),
        generation=generation,
        validation=(
            None
            if grounded is None
            else RagValidationInfo(
                unknown_labels_removed=grounded.removed_unknown_labels,
                uncited_sentences_removed=grounded.removed_uncited_sentences,
                fabricated_quotes_removed=grounded.removed_fabricated_quotes,
                unsupported_literals_removed=grounded.removed_unsupported_literals,
                citation_granularity=grounded.granularity,
            )
        ),
        timings=RagTimings(**outcome.timings),
    )


def citation_reference(item: Citation) -> dict[str, Any]:
    """What is stored for a citation: identifiers and metadata only, never retrieved text."""
    record = item.block.record
    return {
        "label": item.block.label,
        "document_id": str(record.document_id),
        "chunk_id": str(record.chunk_id),
        "document_name": record.document_name,
        "chunk_index": record.chunk_index,
        "section_ordinal": record.section_ordinal,
        "page_number": record.page_number,
        "heading": record.heading,
        "heading_path": list(record.heading_path),
        "start_char": record.start_char,
        "end_char": record.end_char,
        "source_truncated": item.block.truncated,
    }


def assistant_details(response: RagAnswerResponse) -> dict[str, Any]:
    """The safe diagnostics stored with an answer: no prompt, no model output, no key."""
    dumped = response.model_dump(mode="json")
    return {
        key: dumped[key] for key in ("retrieval", "context", "generation", "validation", "timings")
    }
