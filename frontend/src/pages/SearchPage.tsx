import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import DocumentPicker, { type Selection } from "../components/DocumentPicker";
import SourcePanel, { sourceFromHit, type SourceView } from "../components/SourcePanel";
import { Button, EmptyState, ErrorNotice, LoadingBlock, Notice, PageHeader, Select, TextInput, VersionTag } from "../components/ui/primitives";
import { ApiError, isAbort } from "../services/errors";
import { search, type SearchMode } from "../services/searchService";
import type { SearchHit, SearchResponse } from "../types/api";

function foundBy(hit: SearchHit, mode: SearchMode): string {
  const parts: string[] = [];
  if (mode === "semantic") parts.push("Matched by meaning");
  else if (hit.dense_rank != null && hit.sparse_rank != null) parts.push("Matched by meaning and by words");
  else if (hit.dense_rank != null) parts.push("Matched by meaning");
  else if (hit.sparse_rank != null) parts.push("Matched by the words you typed");
  if (hit.retrieval_rank !== undefined && hit.retrieval_rank !== hit.rank) {
    parts.push(`reranked from position ${hit.retrieval_rank} to ${hit.rank}`);
  } else if (hit.retrieval_rank !== undefined) {
    parts.push("kept its position when reranked");
  }
  return parts.join(" · ");
}

function where(hit: SearchHit): string | null {
  const parts = [hit.heading_path.length > 0 ? hit.heading_path.join(" › ") : null, hit.page_number !== null ? `page ${hit.page_number}` : null].filter(Boolean);
  return parts.length > 0 ? parts.join(" · ") : null;
}

export default function SearchPage() {
  const [params, setParams] = useSearchParams();
  const initial = params.get("q") ?? "";
  const [query, setQuery] = useState(initial);
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [rerank, setRerank] = useState(false);
  const [rerankOff, setRerankOff] = useState(false);
  const [topK, setTopK] = useState(10);
  const [docs, setDocs] = useState<Selection>({});
  const [state, setState] = useState<{ status: "idle" | "loading" | "done" | "error"; data?: SearchResponse; error?: unknown; asked?: string; mode?: SearchMode }>({ status: "idle" });
  const [source, setSource] = useState<SourceView | null>(null);
  const controller = useRef<AbortController | null>(null);

  const run = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    setState((s) => ({ ...s, status: "loading", error: undefined }));
    try {
      const data = await search(
        { query: trimmed, mode, rerank: rerank && !rerankOff, top_k: topK, document_ids: Object.keys(docs).length ? Object.keys(docs) : undefined },
        abort.signal,
      );
      if (!abort.signal.aborted) setState({ status: "done", data, asked: trimmed, mode });
    } catch (error) {
      if (abort.signal.aborted || isAbort(error)) return;
      if (error instanceof ApiError && error.code === "RERANKING_DISABLED") {
        setRerankOff(true);
        setRerank(false);
      }
      setState((s) => ({ ...s, status: "error", error }));
    }
  };

  // a search started from another page (?q=...)
  const started = useRef(false);
  useEffect(() => {
    if (initial && !started.current) {
      started.current = true;
      void run(initial);
    }
    return () => controller.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setParams(query.trim() ? { q: query.trim() } : {}, { replace: true });
    void run(query);
  };

  const result = state.data;
  return (
    <>
      <PageHeader title="Search" subtitle="Find the passages in your documents that match a question or a phrase." />
      <form className="panel search-form" onSubmit={submit} role="search">
        <div className="search-row">
          <TextInput type="search" aria-label="Search your documents" placeholder="What are you looking for?" value={query} onChange={(e) => setQuery(e.target.value)} maxLength={2000} autoFocus />
          <Button type="submit" variant="primary" icon="search" busy={state.status === "loading"} disabled={!query.trim()}>
            Search
          </Button>
        </div>
        <div className="options-row">
          <label className="inline-field">
            <span>Method</span>
            <Select value={mode} onChange={(e) => setMode(e.target.value as SearchMode)}>
              <option value="hybrid">Meaning and keywords (recommended)</option>
              <option value="semantic">Meaning only</option>
            </Select>
          </label>
          <label className="inline-field">
            <span>Results</span>
            <Select value={topK} onChange={(e) => setTopK(Number(e.target.value))}>
              {[5, 10, 20].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </Select>
          </label>
          <label className={`check${mode !== "hybrid" || rerankOff ? " check-disabled" : ""}`}>
            <input type="checkbox" checked={rerank && mode === "hybrid"} disabled={mode !== "hybrid" || rerankOff} onChange={(e) => setRerank(e.target.checked)} />
            <span>Rerank the best matches (slower, can improve order)</span>
          </label>
          <div className="picker-row">
            <DocumentPicker selected={docs} onChange={setDocs} />
          </div>
        </div>
        {rerankOff ? <Notice tone="warn">Reranking is not enabled on this server, so it is switched off here.</Notice> : null}
        {Object.keys(docs).length > 0 ? <p className="muted small">Searching only: {Object.values(docs).join(", ")}</p> : null}
      </form>

      <section aria-live="polite" aria-busy={state.status === "loading"}>
        {state.status === "loading" && !result ? <LoadingBlock label="Searching" /> : null}
        {state.status === "error" ? <ErrorNotice error={state.error} onRetry={() => void run(query)} /> : null}
        {state.status === "idle" ? (
          <EmptyState title="Search your documents">
            Results are passages from the active version of each searchable document. Documents that are still processing are not searched yet.
          </EmptyState>
        ) : null}
        {result ? (
          <>
            <p className="results-summary">
              {result.results.length === 0 ? "No results" : `${result.results.length} result${result.results.length > 1 ? "s" : ""}`} for “{state.asked}”
              {state.status === "loading" ? " (updating…)" : ""}
            </p>
            {result.search.degraded && result.search.degraded.length > 0 ? (
              <Notice tone="warn">Part of the search was unavailable ({result.search.degraded.join(", ")}), so these results may be less complete.</Notice>
            ) : null}
            {result.search.rerank && !result.search.rerank.applied ? (
              <Notice tone="warn">Reranking could not be applied ({result.search.rerank.fallback_reason ?? "unavailable"}); results are in the original order.</Notice>
            ) : null}
            {result.results.length === 0 ? (
              <EmptyState title="Nothing matched">Try different words, remove a document filter, or check that your documents have finished processing.</EmptyState>
            ) : (
              <ol className="results">
                {result.results.map((hit) => (
                  <li key={hit.chunk_id} className="result">
                    <div className="result-head">
                      <Link to={`/documents/${hit.document_id}`} className="doc-name">
                        {hit.document_name}
                      </Link>
                      <VersionTag number={hit.version_number} />
                      {where(hit) ? <span className="muted small">{where(hit)}</span> : null}
                    </div>
                    <p className="result-text" dir="auto">
                      {hit.text.length > 420 ? `${hit.text.slice(0, 420).trimEnd()}…` : hit.text}
                    </p>
                    <div className="result-foot">
                      <span className="muted small">{foundBy(hit, state.mode ?? mode)}</span>
                      <Button size="sm" variant="ghost" onClick={() => setSource(sourceFromHit(hit))}>
                        View source
                      </Button>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </>
        ) : null}
      </section>
      <SourcePanel source={source} onClose={() => setSource(null)} />
    </>
  );
}
