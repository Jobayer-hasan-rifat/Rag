import type { SearchRequest, SearchResponse } from "../types/api";
import { post } from "./apiClient";

export type SearchMode = "hybrid" | "semantic";

export interface SearchOptions extends SearchRequest {
  mode: SearchMode;
  /** Reorder the top candidates with the reranker (hybrid only; the server may have it disabled). */
  rerank?: boolean;
}

export async function search(options: SearchOptions, signal?: AbortSignal): Promise<SearchResponse> {
  const { mode, rerank, ...body } = options;
  const payload = mode === "hybrid" && rerank ? { ...body, rerank: true } : body;
  return (await post<SearchResponse>(`/search/${mode}`, payload, { signal })).data;
}
