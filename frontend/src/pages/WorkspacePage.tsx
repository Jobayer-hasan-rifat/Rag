import { useState } from "react";
import { NavLink, useNavigate, useParams } from "react-router-dom";

import Thread from "../components/Thread";
import { useToast } from "../components/ui/Toast";
import { Button, EmptyState, ErrorNotice, LoadingBlock, Pagination } from "../components/ui/primitives";
import { useResource } from "../hooks/useResource";
import { formatRelative } from "../lib/format";
import { createConversation, listConversations } from "../services/conversationService";

export default function WorkspacePage() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const list = useResource((signal) => listConversations(page, signal), [page]);
  const items = list.data?.items ?? [];

  const create = async () => {
    setCreating(true);
    try {
      const created = await createConversation();
      list.reload();
      navigate(`/workspace/${created.id}`);
    } catch (error) {
      toast.error(error);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className={`workspace${conversationId ? " has-thread" : ""}`}>
      <aside className="history" aria-label="Conversations">
        <div className="history-head">
          <h1>AI Workspace</h1>
          <Button variant="primary" size="sm" icon="plus" busy={creating} onClick={() => void create()}>
            New
          </Button>
        </div>
        {list.error && !list.data ? (
          <ErrorNotice error={list.error} onRetry={list.reload} compact />
        ) : !list.data ? (
          <LoadingBlock label="Loading conversations" />
        ) : items.length === 0 ? (
          <p className="muted history-empty">No conversations yet. Start one to ask questions about your documents.</p>
        ) : (
          <>
            <ul className="history-list">
              {items.map((item) => (
                <li key={item.id}>
                  <NavLink to={`/workspace/${item.id}`} className={({ isActive }) => `history-item${isActive ? " active" : ""}`}>
                    <span className="history-title">{item.title}</span>
                    <span className="muted small">
                      {item.message_count} message{item.message_count === 1 ? "" : "s"} · {formatRelative(item.last_activity_at)}
                    </span>
                  </NavLink>
                </li>
              ))}
            </ul>
            <Pagination page={list.data.page.page} totalPages={list.data.page.total_pages} totalItems={list.data.page.total_items} onPage={setPage} noun="conversations" />
          </>
        )}
      </aside>
      <section className="conversation" aria-label="Conversation">
        {conversationId ? (
          <Thread key={conversationId} conversationId={conversationId} onListChange={list.reload} />
        ) : (
          <EmptyState
            title="Ask questions about your documents"
            action={
              <Button variant="primary" icon="plus" busy={creating} onClick={() => void create()}>
                Start a conversation
              </Button>
            }
          >
            Each conversation is saved. Answers cite the passages they used, so you can open the source and check it yourself.
          </EmptyState>
        )}
      </section>
    </div>
  );
}
