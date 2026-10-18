import { Markdown } from "../lib/markdown";
import type { Message, MessageCitation } from "../types/api";
import { Icon } from "./ui/Icon";
import { Button, VersionTag } from "./ui/primitives";

function citationState(citation: MessageCitation): { label: string; tone: "ok" | "warn" | "bad" } {
  if (citation.removed) return { label: "source deleted", tone: "bad" };
  if (!citation.source_available) return { label: "passage unavailable", tone: "bad" };
  if (citation.superseded) return { label: "earlier version", tone: "warn" };
  return { label: "", tone: "ok" };
}

interface Props {
  message: Message;
  onOpenSource: (citation: MessageCitation) => void;
}

/** An assistant answer: rendered Markdown with clickable source markers, then the source list. */
export function AssistantMessage({ message, onOpenSource }: Props) {
  const byLabel = new Map(message.citations.map((citation) => [citation.label, citation]));
  const noEvidence = message.insufficient_evidence === true;
  return (
    <article className={`message message-assistant${noEvidence ? " message-muted" : ""}`} aria-label="Answer">
      <div className="message-body" dir="auto">
        <Markdown
          text={message.content}
          renderCitation={(label, key) => {
            const citation = byLabel.get(label);
            if (!citation) return null;
            const state = citationState(citation);
            return (
              <button
                key={key}
                type="button"
                className={`cite cite-${state.tone}`}
                onClick={() => onOpenSource(citation)}
                aria-label={`Source ${label}: ${citation.document_name ?? "unavailable source"}${state.label ? `, ${state.label}` : ""}`}
              >
                {label}
              </button>
            );
          }}
        />
      </div>
      {noEvidence ? (
        <p className="message-note">
          <Icon name="alert" size={14} /> No passage in your documents answered this question, so no sources are cited.
        </p>
      ) : null}
      {message.citations.length > 0 ? (
        <div className="sources" aria-label="Sources">
          <span className="sources-title">Sources</span>
          <ul>
            {message.citations.map((citation) => {
              const state = citationState(citation);
              return (
                <li key={citation.chunk_id + citation.label}>
                  <button type="button" className="source-chip" onClick={() => onOpenSource(citation)}>
                    <span className={`cite cite-${state.tone}`}>{citation.label}</span>
                    <span className="source-name">{citation.document_name ?? "Deleted source"}</span>
                    <VersionTag number={citation.version_number} tone={citation.superseded ? "warn" : undefined} />
                    {state.label ? <span className={`source-flag source-flag-${state.tone}`}>{state.label}</span> : null}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </article>
  );
}

interface QuestionProps {
  message: Message;
  /** The failed question can be sent again (same idempotency key) from here. */
  onRetry?: () => void;
  retryLabel?: string;
  retrying?: boolean;
}

export function QuestionMessage({ message, onRetry, retryLabel = "Retry", retrying }: QuestionProps) {
  return (
    <article className="message message-user" aria-label="Your question">
      <div className="message-body" dir="auto">
        {message.content.split("\n").map((line, index) => (
          <p key={index}>{line}</p>
        ))}
      </div>
      {message.status === "processing" ? (
        <p className="message-note" role="status">
          <span className="dots" aria-hidden="true" /> Still being answered. It will appear here when it is ready.
        </p>
      ) : null}
      {message.status === "failed" ? (
        <p className="message-note message-note-bad" role="alert">
          <Icon name="alert" size={14} />{" "}
          {message.failure_reason === "interrupted"
            ? "This question was interrupted before it was answered."
            : "This question was saved but not answered."}
          {onRetry ? (
            <Button size="sm" icon="retry" onClick={onRetry} busy={retrying}>
              {retryLabel}
            </Button>
          ) : null}
        </p>
      ) : null}
    </article>
  );
}
