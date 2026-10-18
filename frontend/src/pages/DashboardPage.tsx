import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useUser } from "../auth/AuthContext";
import { DocumentStatus, isBusy, needsRetry } from "../components/documentParts";
import { useToast } from "../components/ui/Toast";
import { Button, EmptyState, ErrorNotice, LoadingBlock, PageHeader, Skeleton, TextInput } from "../components/ui/primitives";
import UploadZone from "../components/UploadZone";
import { useResource } from "../hooks/useResource";
import { usePolling } from "../hooks/useTimers";
import { formatBytes, formatRelative } from "../lib/format";
import { listConversations } from "../services/conversationService";
import { listDocuments, retryDocument, uploadDocument } from "../services/documentService";

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

export default function DashboardPage() {
  const user = useUser();
  const navigate = useNavigate();
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [retrying, setRetrying] = useState<string | null>(null);
  const overview = useResource(loadOverview, []);
  const conversations = useResource((signal) => listConversations(1, signal), []);
  usePolling(overview.reload, 5000, (overview.data?.recent ?? []).some(isBusy));

  const o = overview.data;
  const processing = o ? Math.max(0, o.total - o.ready - o.failed) : 0;

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
      <PageHeader title={`Welcome, ${user.display_name}`} subtitle="Your documents, searchable and ready to question." />

      <form className="panel hero-search" onSubmit={search} role="search">
        <TextInput type="search" aria-label="Search your documents" placeholder="Search your documents…" value={query} onChange={(e) => setQuery(e.target.value)} />
        <Button type="submit" variant="primary" icon="search" disabled={!query.trim()}>
          Search
        </Button>
        <Link className="btn btn-secondary" to="/workspace">
          Ask a question
        </Link>
      </form>

      {overview.error && !o ? (
        <ErrorNotice error={overview.error} onRetry={overview.reload} />
      ) : !o ? (
        <Skeleton lines={4} height={18} />
      ) : o.total === 0 ? (
        <section className="panel">
          <EmptyState title="Start by adding a document">Upload a PDF, Word, text or Markdown file. Once it is processed you can search it and ask questions about it.</EmptyState>
          <UploadZone
            upload={(file, handlers) => uploadDocument(file, handlers)}
            describe={(file) => ({ message: `“${file.name}” was uploaded and is being processed.`, link: { to: "/documents", label: "Follow it in Documents" } })}
            onFinished={overview.reload}
          />
        </section>
      ) : (
        <>
          <section className="stats" aria-label="Library overview">
            <Link to="/documents" className="stat">
              <span className="stat-value">{o.total}</span>
              <span className="stat-label">Documents</span>
            </Link>
            <Link to="/documents" className="stat">
              <span className="stat-value">{o.ready}</span>
              <span className="stat-label">Searchable</span>
            </Link>
            <Link to="/documents" className="stat">
              <span className="stat-value">{processing}</span>
              <span className="stat-label">Processing</span>
            </Link>
            <Link to="/documents" className={`stat${o.failed ? " stat-bad" : ""}`}>
              <span className="stat-value">{o.failed}</span>
              <span className="stat-label">Need attention</span>
            </Link>
          </section>

          {o.attention.length > 0 ? (
            <section className="panel" aria-labelledby="attention-heading">
              <h2 id="attention-heading">Needs attention</h2>
              <ul className="plain-list">
                {o.attention.filter(needsRetry).map((doc) => (
                  <li key={doc.id} className="list-row">
                    <div>
                      <Link to={`/documents/${doc.id}`} className="doc-name">
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
            <section className="panel" aria-labelledby="recent-docs">
              <div className="toolbar">
                <h2 id="recent-docs">Recent documents</h2>
                <Link to="/documents">All documents</Link>
              </div>
              <ul className="plain-list">
                {o.recent.map((doc) => (
                  <li key={doc.id} className="list-row">
                    <div>
                      <Link to={`/documents/${doc.id}`} className="doc-name">
                        {doc.filename}
                      </Link>
                      <span className="muted small">
                        {" "}
                        {formatBytes(doc.file_size)} · {formatRelative(doc.created_at)}
                      </span>
                      <DocumentStatus doc={doc} />
                    </div>
                  </li>
                ))}
              </ul>
              <UploadZone
                compact
                upload={(file, handlers) => uploadDocument(file, handlers)}
                describe={(file) => ({ message: `“${file.name}” was uploaded and is being processed.` })}
                onFinished={overview.reload}
              />
            </section>

            <section className="panel" aria-labelledby="recent-chats">
              <div className="toolbar">
                <h2 id="recent-chats">Recent conversations</h2>
                <Link to="/workspace">Open workspace</Link>
              </div>
              {conversations.error && !conversations.data ? (
                <ErrorNotice error={conversations.error} onRetry={conversations.reload} compact />
              ) : !conversations.data ? (
                <LoadingBlock label="Loading conversations" />
              ) : conversations.data.items.length === 0 ? (
                <EmptyState title="No conversations yet" action={<Link className="btn btn-secondary" to="/workspace">Start one</Link>}>
                  Ask a question and the answer will cite your documents.
                </EmptyState>
              ) : (
                <ul className="plain-list">
                  {conversations.data.items.slice(0, 5).map((item) => (
                    <li key={item.id} className="list-row">
                      <div>
                        <Link to={`/workspace/${item.id}`} className="doc-name">
                          {item.title}
                        </Link>
                        <span className="muted small"> {formatRelative(item.last_activity_at)}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </>
      )}
    </>
  );
}
