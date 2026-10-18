import { failureText, isProcessing } from "../lib/format";
import type { DocumentItem } from "../types/api";
import { StatusPill } from "./ui/primitives";

/** Is anything about this document still being processed (the shown or a newer version)? */
export function isBusy(doc: DocumentItem): boolean {
  return isProcessing(doc.status) || (doc.latest_version ? isProcessing(doc.latest_version.status) : false);
}

export function needsRetry(doc: DocumentItem): boolean {
  return doc.status === "failed" || doc.latest_version?.status === "failed";
}

/** One line about versions: which is shown and what the newest one is doing. */
export function versionNote(doc: DocumentItem): string {
  const base = doc.version_count > 1 ? `Version ${doc.version_number} of ${doc.version_count}` : "Version 1";
  const latest = doc.latest_version;
  if (!latest) return base;
  if (latest.status === "failed") return `${base} · newer version ${latest.version_number} failed`;
  if (latest.status === "ready") return `${base} · newer version ${latest.version_number} ready`;
  return `${base} · newer version ${latest.version_number} processing`;
}

export function DocumentStatus({ doc }: { doc: DocumentItem }) {
  return (
    <div className="doc-status">
      <StatusPill status={doc.status} />
      {doc.status === "failed" ? <span className="muted small">{doc.error_message ?? failureText(doc.failure_reason)}</span> : null}
      {doc.status !== "ready" && doc.status !== "failed" ? <span className="muted small">Not searchable yet</span> : null}
      {doc.latest_version && doc.latest_version.status === "failed" && doc.status !== "failed" ? (
        <span className="muted small">{failureText(doc.latest_version.failure_reason)}</span>
      ) : null}
    </div>
  );
}
