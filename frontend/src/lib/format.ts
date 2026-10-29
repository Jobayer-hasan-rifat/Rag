import type { DocumentStatus } from "../types/api";

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

const DATE = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" });
const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
const RELATIVE = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export const formatDate = (iso: string): string => DATE.format(new Date(iso));
export const formatDateTime = (iso: string): string => DATE_TIME.format(new Date(iso));

export function formatRelative(iso: string, now: number = Date.now()): string {
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  const steps: [Intl.RelativeTimeFormatUnit, number][] = [
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  for (const [unit, size] of steps) {
    if (Math.abs(seconds) >= size) return RELATIVE.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

export const PROCESSING_STATUSES: readonly DocumentStatus[] = [
  "pending",
  "parsing",
  "chunking",
  "chunked",
  "embedding",
  "indexing",
];

export const isProcessing = (status: DocumentStatus): boolean => PROCESSING_STATUSES.includes(status);

export const STATUS_LABEL: Record<DocumentStatus, string> = {
  pending: "Queued",
  parsing: "Reading file",
  chunking: "Splitting text",
  chunked: "Preparing index",
  embedding: "Indexing",
  indexing: "Indexing",
  ready: "Ready",
  failed: "Failed",
};

export const STATUS_HELP: Record<DocumentStatus, string> = {
  pending: "Uploaded and waiting to be processed. Not searchable yet.",
  parsing: "The text is being extracted. Not searchable yet.",
  chunking: "The text is being split into passages. Not searchable yet.",
  chunked: "Waiting to be indexed. Not searchable yet.",
  embedding: "Passages are being indexed. Not searchable yet.",
  indexing: "Passages are being indexed. Not searchable yet.",
  ready: "Processed and searchable.",
  failed: "Processing failed.",
};

const FAILURE_TEXT: Record<string, string> = {
  empty_document: "No readable text was found. A scanned file needs OCR, which may be off or may not have recognised this scan.",
  corrupt_document: "The file appears to be damaged.",
  encrypted_document: "The file is password protected, so its text cannot be read.",
  unsupported_format: "The file format is not supported.",
  too_many_pages: "The document has more pages than can be processed.",
  content_too_large: "The document has more text than can be processed.",
  too_many_chunks: "The document is too large to index.",
  storage_missing: "The stored file could not be found.",
  storage_unavailable: "The file store was unavailable.",
  extraction_failed: "The text could not be extracted from this file.",
  chunking_failed: "The text could not be split into passages.",
  timeout: "Processing took too long.",
  retries_exhausted: "Processing was retried and kept failing.",
  database_error: "A temporary database problem interrupted processing.",
  model_unavailable: "The indexing model was not available.",
  embedding_failed: "The indexing model failed on this text.",
  invalid_embedding: "The indexing model returned unusable data.",
};

export function failureText(reason: string | null): string {
  if (!reason) return "Processing failed.";
  return FAILURE_TEXT[reason] ?? "Processing failed.";
}

export const SUPPORTED_EXTENSIONS = [".pdf", ".docx", ".txt", ".md"] as const;
export const ACCEPT = SUPPORTED_EXTENSIONS.join(",");

export function hasSupportedExtension(name: string): boolean {
  const lower = name.toLowerCase();
  return SUPPORTED_EXTENSIONS.some((extension) => lower.endsWith(extension));
}
