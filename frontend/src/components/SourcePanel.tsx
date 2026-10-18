import type { HTMLAttributes } from "react";
import { Link } from "react-router-dom";

import type { MessageCitation, SearchHit } from "../types/api";
import { Dialog } from "./ui/Dialog";
import { Icon } from "./ui/Icon";
import { IconButton, Notice, VersionTag } from "./ui/primitives";

/** Which copy of the document the passage came from, in words the reader can act on. */
export type SourceState = "active" | "earlier" | "deleted" | "unavailable";

/** What the panel shows, whichever API produced it. */
export interface SourceView {
  title: string;
  /** The marker in the answer, such as "S1"; absent for a search result. */
  label?: string;
  documentId: string | null;
  versionNumber: number | null;
  state: SourceState;
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
    state: "active",
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
  const state: SourceState = citation.removed ? "deleted" : !citation.source_available ? "unavailable" : citation.superseded ? "earlier" : "active";
  return {
    title: citation.document_name ?? (citation.removed ? "Deleted source" : "Unavailable source"),
    label: citation.label,
    documentId: citation.removed ? null : citation.document_id,
    versionNumber: citation.version_number,
    state,
    location: location(citation.heading_path, citation.page_number),
    text: citation.excerpt,
    textIsExcerpt: citation.excerpt !== null && (citation.excerpt_truncated ?? false),
    notes,
  };
}

const STATE_TEXT: Record<SourceState, string> = {
  active: "Active version",
  earlier: "Earlier version",
  deleted: "Deleted",
  unavailable: "Passage unavailable",
};

function SourceBody({ source, onNavigate }: { source: SourceView; onNavigate: () => void }) {
  return (
    <div className="source">
      <p className="source-meta">
        {source.label ? <span className="cite cite-static">{source.label}</span> : null}
        <VersionTag number={source.versionNumber} tone={source.state === "earlier" ? "warn" : undefined} />
        <span className={`state state-${source.state}`}>
          <Icon name={source.state === "active" ? "check" : "alert"} size={13} />
          {STATE_TEXT[source.state]}
        </span>
      </p>
      <h3>{source.title}</h3>
      {source.location ? <p className="muted">{source.location}</p> : null}
      {source.notes.map((note) => (
        <Notice key={note.text} tone={note.tone === "warn" ? "warn" : "info"}>
          {note.text}
        </Notice>
      ))}
      {source.text !== null ? (
        <blockquote className="source-text" dir="auto">
          {source.text}
          {source.textIsExcerpt ? <span className="muted"> …</span> : null}
        </blockquote>
      ) : (
        <p className="muted">No passage text is available for this source.</p>
      )}
      <p className="muted small">A source shows which passage was used. It does not prove the answer is right: read it and judge whether it supports the claim.</p>
      {source.documentId ? (
        <Link className="btn btn-secondary btn-md" to={`/documents/${source.documentId}`} onClick={onNavigate}>
          <Icon name="file" size={16} />
          <span>Open document</span>
        </Link>
      ) : null}
    </div>
  );
}

interface Props {
  source: SourceView | null;
  onClose: () => void;
  /** Render beside the conversation instead of over it; the handle makes the panel resizable. */
  docked?: { width: number; handle: HTMLAttributes<HTMLDivElement> };
}

export default function SourcePanel({ source, onClose, docked }: Props) {
  if (docked) {
    if (!source) return null;
    return (
      <aside className="evidence" style={{ width: docked.width }} aria-label="Source">
        <div className="evidence-resizer" aria-label="Resize the source panel" {...docked.handle} />
        <header className="evidence-head">
          <h2>Source</h2>
          <IconButton icon="x" label="Close source panel" onClick={onClose} />
        </header>
        <div className="evidence-body">
          <SourceBody source={source} onNavigate={onClose} />
        </div>
      </aside>
    );
  }
  return (
    <Dialog open={source !== null} variant="drawer" title="Source" onClose={onClose}>
      {source ? <SourceBody source={source} onNavigate={onClose} /> : null}
    </Dialog>
  );
}
