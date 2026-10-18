import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useUser } from "../auth/AuthContext";
import { DocumentStatus, isBusy, needsRetry } from "../components/documentParts";
import { useUploadDialog } from "../components/UploadDialog";
import { Icon } from "../components/ui/Icon";
import { useToast } from "../components/ui/Toast";
import { Button, EmptyState, ErrorNotice, LoadingBlock, PageHeader, Skeleton, TextInput } from "../components/ui/primitives";
import { useAskAbout } from "../hooks/useAskAbout";
import { useResource } from "../hooks/useResource";
import { usePolling } from "../hooks/useTimers";
import { formatBytes, formatRelative } from "../lib/format";
import { useOnLibraryChanged } from "../lib/libraryEvents";
import { listConversations } from "../services/conversationService";
import { listDocuments, retryDocument } from "../services/documentService";

interface Overview {
  total: number;
  ready: number;
  failed: number;
  recent: Awaited<ReturnType<typeof listDocuments>>["items"];
  attention: Awaited<ReturnType<typeof listDocuments>>["items"];
}

async function loadOverview(signal: AbortSignal): Promise<Overview> {
  const base = { page: 1, sort: "created_at", order: "desc" } as const;
  const [recent, ready, failed] = await Promise.all([
    listDocuments({ ...base, page_size: 5 }, signal),
    listDocuments({ ...base, page_size: 1, status: "ready" }, signal),
    listDocuments({ ...base, page_size: 5, status: "failed" }, signal),
  ]);
  return {
    total: recent.page.total_items,
    ready: ready.page.total_items,
    failed: failed.page.total_items,
    recent: recent.items,
    attention: failed.items,
  };
}

/** One sentence about the library, from counts the API returns. */
function librarySummary(o: Overview): string {
  const processing = Math.max(0, o.total - o.ready - o.failed);
  const parts = [`${o.total} document${o.total === 1 ? "" : "s"}`, `${o.ready} ready to search`];
  if (processing > 0) parts.push(`${processing} processing`);
  if (o.failed > 0) parts.push(`${o.failed} failed`);
  return parts.join(" · ");
}

function GetStarted({ onUpload, hasReady, onAsk }: { onUpload: () => void; hasReady: boolean; onAsk: () => void }) {
  return (
    <section className="panel steps" aria-labelledby="start-heading">
      <h2 id="start-heading">Get started in three steps</h2>
      <ol>
        <li className="step step-now">
          <span className="step-num" aria-hidden="true">1</span>
          <div>
            <h3>Add a document</h3>
            <p>PDF, Word, text or Markdown. Your files are private to your account.</p>
            <Button variant="primary" icon="upload" onClick={onUpload}>
              Add documents
            </Button>
          </div>
        </li>
        <li className="step">
          <span className="step-num" aria-hidden="true">2</span>
          <div>
            <h3>Wait for “Ready”</h3>
            <p>Each document is read, split into passages and indexed. It can be searched once it shows as Ready; this usually takes a short while.</p>
          </div>
        </li>
        <li className="step">
          <span className="step-num" aria-hidden="true">3</span>
          <div>
            <h3>Ask and check the sources</h3>
            <p>Every claim in an answer points to the passage it came from. Open the source to read the original.</p>
            {hasReady ? (
              <Button icon="chat" onClick={onAsk}>
                Ask a question
              </Button>
            ) : null}
          </div>
        </li>
      </ol>
    </section>
  );
}

export default function DashboardPage() {
  const user = useUser();
  const navigate = useNavigate();
  const toast = useToast();
  const upload = useUploadDialog();
  const { ask, busy: asking } = useAskAbout();
  const [query, setQuery] = useState("");
  const [retrying, setRetrying] = useState<string | null>(null);
  const overview = useResource(loadOverview, []);
  const conversations = useResource((signal) => listConversations(1, signal), []);
  usePolling(overview.reload, 5000, (overview.data?.recent ?? []).some(isBusy));
  useOnLibraryChanged(overview.reload);

  const o = overview.data;

  const search = (event: FormEvent) => {
    event.preventDefault();
    if (query.trim()) navigate(`/search?q=${encodeURIComponent(query.trim())}`);
  };

  const retry = async (id: string, name: string) => {
    setRetrying(id);
    try {
      await retryDocument(id);
      toast.success(`Processing of “${name}” was queued again.`);
      overview.reload();
    } catch (error) {
      toast.error(error);
    } finally {
      setRetrying(null);
    }
  };

  return (
    <>
      <PageHeader
        title={o && o.total > 0 ? `Welcome back, ${user.display_name}` : `Welcome, ${user.display_name}`}
        subtitle={o && o.total > 0 ? librarySummary(o) : "Search and ask questions across your own documents, with sources you can check."}
        actions={
          o && o.total > 0 ? (
            <Button variant="primary" icon="chat" busy={asking} onClick={() => void ask()}>
              Ask a question
            </Button>
          ) : undefined
        }
      />

      {overview.error && !o ? (
        <ErrorNotice error={overview.error} onRetry={overview.reload} />
      ) : !o ? (
        <Skeleton lines={4} height={18} />
      ) : o.total === 0 ? (
        <GetStarted onUpload={upload.open} hasReady={false} onAsk={() => void ask()} />
      ) : (
        <>
          <form className="quick-search" onSubmit={search} role="search">
            <TextInput type="search" aria-label="Search your documents" placeholder="Search the passages in your documents…" value={query} onChange={(e) => setQuery(e.target.value)} />
            <Button type="submit" variant="primary" icon="search" disabled={!query.trim()}>
              Search
            </Button>
          </form>

          {o.attention.filter(needsRetry).length > 0 ? (
            <section className="panel panel-attention" aria-labelledby="attention-heading">
              <h2 id="attention-heading">
                <Icon name="alert" size={18} /> Needs attention
              </h2>
              <ul className="plain-list">
                {o.attention.filter(needsRetry).map((doc) => (
                  <li key={doc.id} className="list-row">
                    <div className="list-main">
                      <Link to={`/documents/${doc.id}`} className="doc-name" dir="auto">
                        {doc.filename}
                      </Link>
                      <DocumentStatus doc={doc} />
                    </div>
                    <Button size="sm" icon="retry" busy={retrying === doc.id} onClick={() => void retry(doc.id, doc.filename)}>
                      Retry
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <div className="two-col">
            <section className="panel" aria-labelledby="recent-chats">
              <div className="toolbar">
                <h2 id="recent-chats">Continue a conversation</h2>
                <Link to="/workspace">All conversations</Link>
              </div>
              {conversations.error && !conversations.data ? (
                <ErrorNotice error={conversations.error} onRetry={conversations.reload} compact />
              ) : !conversations.data ? (
                <LoadingBlock label="Loading conversations" />
              ) : conversations.data.items.length === 0 ? (
                <EmptyState title="No conversations yet" action={<Button icon="chat" busy={asking} onClick={() => void ask()}>Ask your first question</Button>}>
                  Answers cite your documents, and each conversation is saved.
                </EmptyState>
              ) : (
                <ul className="plain-list">
                  {conversations.data.items.slice(0, 5).map((item) => (
                    <li key={item.id} className="list-row">
                      <div className="list-main">
                        <Link to={`/workspace/${item.id}`} className="doc-name" dir="auto">
                          {item.title}
                        </Link>
                        <span className="muted small">{formatRelative(item.last_activity_at)}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="panel" aria-labelledby="recent-docs">
              <div className="toolbar">
                <h2 id="recent-docs">Recent documents</h2>
                <Link to="/documents">All documents</Link>
              </div>
              <ul className="plain-list">
                {o.recent.map((doc) => (
                  <li key={doc.id} className="list-row">
                    <div className="list-main">
                      <Link to={`/documents/${doc.id}`} className="doc-name" dir="auto">
                        {doc.filename}
                      </Link>
                      <span className="muted small">
                        {formatBytes(doc.file_size)} · {formatRelative(doc.created_at)}
                      </span>
                      <DocumentStatus doc={doc} />
                    </div>
                  </li>
                ))}
              </ul>
              <Button icon="upload" onClick={upload.open}>
                Add documents
              </Button>
            </section>
          </div>
        </>
      )}
    </>
  );
}
