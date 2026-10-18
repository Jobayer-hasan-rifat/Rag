import { Link } from "react-router-dom";

import type { MessageCitation, SearchHit } from "../types/api";
import { Dialog } from "./ui/Dialog";
import { Notice, VersionTag } from "./ui/primitives";

/** What the panel shows, whichever API produced it. */
export interface SourceView {
  title: string;
  documentId: string | null;
  versionNumber: number | null;
  location: string | null;
  /** The passage text, or null when it can no longer be shown. */
  text: string | null;
  textIsExcerpt: boolean;
  notes: { tone: "info" | "warn"; text: string }[];
}

function location(heading: string[], page: number | null): string | null {
  const parts = [heading.length > 0 ? heading.join(" › ") : null, page !== null ? `page ${page}` : null].filter(Boolean);
  return parts.length > 0 ? parts.join(" · ") : null;
}

export function sourceFromHit(hit: SearchHit): SourceView {
  return {
    title: hit.document_name,
    documentId: hit.document_id,
    versionNumber: hit.version_number ?? null,
    location: location(hit.heading_path, hit.page_number),
    text: hit.text,
    textIsExcerpt: false,
    notes: [],
  };
}

/**
 * A stored citation. The text is the excerpt the server read from the cited version's own chunk;
 * it is never replaced by text from another version, and when the server cannot show it (the
 * version or document was deleted, or the passages were rebuilt) nothing is shown instead.
 */
export function sourceFromCitation(citation: MessageCitation): SourceView {
  const notes: SourceView["notes"] = [];
  if (citation.removed) {
    notes.push({ tone: "warn", text: "The document or version this answer cited was deleted. Only its identifier is kept." });
  } else if (!citation.source_available) {
    notes.push({
      tone: "warn",
      text: "The cited passage can no longer be shown (the document was re-processed or is not searchable). Its location is kept.",
    });
  }
  if (citation.superseded && citation.source_available) {
    notes.push({ tone: "info", text: "This answer used an earlier version. A newer version is now active; the text below is from the earlier one." });
  }
  if (citation.version_id === null && citation.source_available) {
    notes.push({ tone: "info", text: "This answer was saved before versions existed, so it refers to the document's first version." });
  }
  return {
    title: citation.document_name ?? (citation.removed ? "Deleted source" : "Unavailable source"),
    documentId: citation.removed ? null : citation.document_id,
    versionNumber: citation.version_number,
    location: location(citation.heading_path, citation.page_number),
    text: citation.excerpt,
    textIsExcerpt: citation.excerpt !== null && (citation.excerpt_truncated ?? false),
    notes,
  };
}

export default function SourcePanel({ source, onClose }: { source: SourceView | null; onClose: () => void }) {
  return (
    <Dialog open={source !== null} variant="drawer" title="Source" onClose={onClose}>
      {source ? (
        <div className="source">
          <h3>{source.title}</h3>
          <p className="source-meta">
            <VersionTag number={source.versionNumber} />
            {source.location ? <span>{source.location}</span> : null}
          </p>
          {source.notes.map((note) => (
            <Notice key={note.text} tone={note.tone === "warn" ? "warn" : "info"}>
              {note.text}
            </Notice>
          ))}
          {source.text !== null ? (
            <blockquote className="source-text lang-auto">
              {source.text}
              {source.textIsExcerpt ? <span className="muted"> …</span> : null}
            </blockquote>
          ) : (
            <p className="muted">No passage text is available for this source.</p>
          )}
          <p className="muted small">
            A source shows which passage was used; it does not prove the answer is right. Read it and judge whether it supports the claim.
          </p>
          {source.documentId ? (
            <Link className="btn btn-secondary btn-sm" to={`/documents/${source.documentId}`} onClick={onClose}>
              Open document
            </Link>
          ) : null}
        </div>
      ) : null}
    </Dialog>
  );
}
