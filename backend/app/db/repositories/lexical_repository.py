import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from sqlalchemy import ARRAY, Text, bindparam, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.lexical_query import LexicalQuery


@dataclass(frozen=True)
class LexicalCandidate:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    score: float  # sum of the matched terms' IDF weights; meaningful only within one query


# The query text is fixed; every user-controlled value is a bound parameter. Optional filters add
# fixed fragments, never user text.
_SQL = """
WITH ready AS (
    SELECT id FROM documents
    WHERE user_id = :u AND status = 'ready'{doc_filter}{collection_filter}
),
terms AS (
    SELECT phrase AS term, 1 AS priority FROM unnest(CAST(:phrases AS text[])) AS phrase
    UNION
    SELECT lexeme, 0 FROM unnest(tsvector_to_array(to_tsvector('simple', :words))) AS lexeme
    WHERE lexeme <> ALL(CAST(:stopwords AS text[]))
),
corpus AS (
    SELECT COALESCE(sum(chunk_count), 0)::float AS n
    FROM documents WHERE user_id = :u AND status = 'ready'
),
present AS (
    SELECT t.term, t.priority FROM terms t
    WHERE EXISTS (SELECT 1 FROM document_chunks c
                  WHERE c.user_id = :u AND c.search_vector @@ phraseto_tsquery('simple', t.term))
),
frequency AS (
    SELECT t.term,
           (SELECT count(*) FROM document_chunks c
             WHERE c.user_id = :u
               AND c.document_id IN (
                   SELECT id FROM documents WHERE user_id = :u AND status = 'ready')
               AND c.search_vector @@ phraseto_tsquery('simple', t.term)) AS n
    FROM (SELECT term FROM present ORDER BY priority DESC, char_length(term) DESC, term
          LIMIT :scan_terms) t
),
weights AS (
    SELECT f.term,
           ln(1 + (GREATEST(corpus.n, f.n) - f.n + 0.5) / (f.n + 0.5)) AS idf
    FROM frequency f, corpus
    WHERE f.n > 0
    ORDER BY idf DESC, f.term
    LIMIT :max_terms
)
SELECT c.id, c.document_id, sum(w.idf) AS score
FROM weights w
JOIN document_chunks c
  ON c.user_id = :u AND c.search_vector @@ phraseto_tsquery('simple', w.term)
WHERE c.document_id IN (SELECT id FROM ready)
GROUP BY c.id, c.document_id
ORDER BY score DESC, c.id
LIMIT :limit
"""


class LexicalRepository:
    """Lexical candidates from PostgreSQL full-text search over the caller's own chunks.

    Words are tokenised by PostgreSQL's `simple` configuration (no stemming or stop words, which
    is the honest choice for Bengali). Each query term is weighted by its inverse document
    frequency within the caller's own corpus, because `ts_rank` has no IDF and would let common
    words outweigh a rare identifier. The composite GIN index `(user_id, search_vector)` keeps the
    scan to the caller's postings. Only `ready` documents are considered.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def candidates(
        self,
        *,
        user_id: uuid.UUID,
        query: LexicalQuery,
        limit: int,
        max_terms: int,
        document_ids: Sequence[uuid.UUID] | None = None,
        collection_id: uuid.UUID | None = None,
        stopwords: Collection[str] = frozenset(),
        timeout_ms: int | None = None,
    ) -> list[LexicalCandidate]:
        """Up to `limit` chunks, best first. Nothing searchable in the query gives no rows
        without touching the database. Terms that occur nowhere in the caller's chunks are
        dropped first (a cheap existence check); if more than the scan limit remain, phrases
        come first, then longer words (rare words and identifiers are usually longer than
        function words). `timeout_ms` bounds the statement. `stopwords` are dropped from the
        free words (not from quoted phrases)."""
        if not query.searchable:
            return []
        doc_filter = " AND id = ANY(:docs)" if document_ids else ""
        collection_filter = (
            " AND id IN (SELECT document_id FROM document_collections "
            "WHERE collection_id = :coll AND user_id = :u)"
            if collection_id is not None
            else ""
        )
        statement = text(
            _SQL.format(doc_filter=doc_filter, collection_filter=collection_filter)
        ).bindparams(
            bindparam("phrases", type_=ARRAY(Text)),
            bindparam("stopwords", type_=ARRAY(Text)),
        )
        params: dict[str, object] = {
            "u": user_id,
            "words": query.words,
            "phrases": list(query.phrases),
            "stopwords": sorted(stopwords),
            "max_terms": max_terms,
            "scan_terms": max_terms * 3,
            "limit": limit,
        }
        if document_ids:
            statement = statement.bindparams(bindparam("docs", type_=ARRAY(UUID(as_uuid=True))))
            params["docs"] = list(document_ids)
        if collection_id is not None:
            params["coll"] = collection_id
        statement = statement.bindparams(bindparam("u", type_=UUID(as_uuid=True)))
        if timeout_ms is not None:
            await self._session.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
        result = await self._session.execute(statement, params)
        return [LexicalCandidate(row[0], row[1], float(row[2])) for row in result.all()]
