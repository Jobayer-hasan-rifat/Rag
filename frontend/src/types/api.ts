/** Request and response shapes of the backend API (see docs/05_API_SPECIFICATION.md). */

export interface Meta {
  request_id: string | null;
  timestamp: string;
}

export interface PageMeta extends Meta {
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
}

export interface Envelope<T> {
  data: T;
  meta: Meta;
}

export interface Paged<T> {
  items: T[];
  page: PageMeta;
}

// --- authentication ---------------------------------------------------------------------------

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: string;
  is_active: boolean;
  created_at: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

// --- documents --------------------------------------------------------------------------------

export type DocumentStatus =
  | "pending"
  | "parsing"
  | "chunking"
  | "chunked"
  | "embedding"
  | "indexing"
  | "ready"
  | "failed";

export interface VersionSummary {
  id: string;
  version_number: number;
  status: DocumentStatus;
  failure_reason: string | null;
}

export interface CollectionSummary {
  id: string;
  name: string;
}

/** A document, described by its current version. `id` is stable across versions. */
export interface DocumentItem {
  id: string;
  version_id: string;
  version_number: number;
  version_count: number;
  active_version_id: string | null;
  latest_version: VersionSummary | null;
  filename: string;
  file_type: string;
  content_type: string;
  file_size: number;
  checksum_sha256: string;
  status: DocumentStatus;
  error_message: string | null;
  failure_reason: string | null;
  processing_started_at: string | null;
  processing_completed_at: string | null;
  page_count: number | null;
  character_count: number | null;
  chunk_count: number | null;
  chunking_version: string | null;
  embedded_chunk_count: number | null;
  embedding_completed_at: string | null;
  searchable: boolean;
  collections: CollectionSummary[];
  created_at: string;
  updated_at: string;
}

export interface DocumentVersion {
  id: string;
  document_id: string;
  version_number: number;
  filename: string;
  file_type: string;
  content_type: string;
  file_size: number;
  checksum_sha256: string;
  status: DocumentStatus;
  error_message: string | null;
  failure_reason: string | null;
  is_active: boolean;
  is_current: boolean;
  searchable: boolean;
  chunk_count: number | null;
  embedded_chunk_count: number | null;
  processing_started_at: string | null;
  processing_completed_at: string | null;
  embedding_completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface VersionUpload {
  version: DocumentVersion;
  created: boolean;
}

export type SortField = "created_at" | "filename" | "file_size";

export interface DocumentQuery {
  page: number;
  page_size: number;
  status?: DocumentStatus | "";
  file_type?: string;
  search?: string;
  sort?: SortField;
  order?: "asc" | "desc";
}

// --- search -----------------------------------------------------------------------------------

export interface SearchRequest {
  query: string;
  top_k?: number;
  document_ids?: string[];
}

export interface SearchHit {
  rank: number;
  chunk_id: string;
  document_id: string;
  version_id?: string | null;
  version_number?: number | null;
  document_name: string;
  chunk_index: number;
  section_ordinal: number;
  page_number: number | null;
  heading: string | null;
  heading_path: string[];
  text: string;
  start_char: number;
  end_char: number;
  // present for hybrid results
  dense_rank?: number | null;
  sparse_rank?: number | null;
  // present when the result was reranked
  retrieval_rank?: number;
  rerank_truncated?: boolean | null;
}

export interface RerankInfo {
  requested?: boolean;
  applied: boolean;
  fallback_reason: string | null;
  candidates: number;
  latency_ms: number;
}

export interface SearchInfo {
  returned: number;
  top_k: number;
  latency_ms: number;
  degraded?: string[];
  retrievers_used?: string[];
  rerank?: RerankInfo | null;
}

export interface SearchResponse {
  results: SearchHit[];
  search: SearchInfo;
}

// --- conversations ----------------------------------------------------------------------------

export interface Conversation {
  id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  last_activity_at: string;
}

export interface MessageCitation {
  label: string;
  document_id: string;
  version_id: string | null;
  version_number: number | null;
  chunk_id: string;
  document_name: string | null;
  chunk_index: number | null;
  section_ordinal: number | null;
  page_number: number | null;
  heading: string | null;
  heading_path: string[];
  start_char: number | null;
  end_char: number | null;
  excerpt: string | null;
  excerpt_truncated: boolean | null;
  source_truncated: boolean | null;
  source_available: boolean;
  removed: boolean;
  superseded: boolean;
}

export type MessageStatus = "processing" | "answered" | "failed" | "complete";

export interface Message {
  id: string;
  sequence: number;
  role: "user" | "assistant";
  status: MessageStatus;
  content: string;
  created_at: string;
  reply_to_id: string | null;
  failure_reason: string | null;
  insufficient_evidence: boolean | null;
  insufficient_reason: string | null;
  citations: MessageCitation[];
  details: Record<string, unknown> | null;
}

export interface ConversationDetail {
  conversation: Conversation;
  messages: Message[];
  page: { page: number; page_size: number; total_messages: number; total_pages: number; order: "asc" | "desc" };
}

export interface SendMessageRequest {
  content: string;
  document_ids?: string[];
  rerank?: boolean;
}

export interface SendMessageResponse {
  conversation: Conversation;
  user_message: Message;
  assistant_message: Message;
  replayed: boolean;
}
