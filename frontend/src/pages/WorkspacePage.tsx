import { useEffect, useState } from "react";
import { NavLink, useParams } from "react-router-dom";

import Thread from "../components/Thread";
import { Icon } from "../components/ui/Icon";
import { Button, EmptyState, ErrorNotice, LoadingBlock, Pagination } from "../components/ui/primitives";
import { useAskAbout } from "../hooks/useAskAbout";
import { useResource } from "../hooks/useResource";
import { formatRelative } from "../lib/format";
import { listConversations } from "../services/conversationService";

const HISTORY_KEY = "rag.ui.history.open";

function initialHistoryOpen(): boolean {
  try {
    return localStorage.getItem(HISTORY_KEY) !== "0";
  } catch {
    return true;
  }
}

export default function WorkspacePage() {
  const { conversationId } = useParams();
  const [page, setPage] = useState(1);
  const [historyOpen, setHistoryOpen] = useState(initialHistoryOpen);
  const { ask, busy } = useAskAbout();
  const list = useResource((signal) => listConversations(page, signal), [page]);
  const items = list.data?.items ?? [];
  const reloadList = list.reload;

  // a conversation created elsewhere (a document page, search) is not in the list yet
  useEffect(() => {
    if (conversationId) reloadList();
  }, [conversationId, reloadList]);

  const toggleHistory = () =>
    setHistoryOpen((value) => {
      try {
        localStorage.setItem(HISTORY_KEY, value ? "0" : "1");
      } catch {
        // a preference only
      }
      return !value;
    });

  return (
    <div className={`workspace${conversationId ? " has-thread" : ""}${historyOpen ? "" : " history-hidden"}`}>
      <h1 className="visually-hidden">Workspace</h1>
      <aside className="history" aria-label="Conversations">
        <div className="history-head">
          <h2>Conversations</h2>
          <Button variant="primary" size="sm" icon="plus" busy={busy} onClick={() => void ask()}>
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
                    <span className="history-title" dir="auto">
                      {item.title}
                    </span>
                    <span className="history-meta">
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
      <div className="conversation">
        {conversationId ? (
          <Thread key={conversationId} conversationId={conversationId} onListChange={list.reload} historyOpen={historyOpen} onToggleHistory={toggleHistory} />
        ) : (
          <div className="workspace-empty">
            <Icon name="book" size={28} />
            <EmptyState
              title="Ask questions about your documents"
              action={
                <Button variant="primary" icon="plus" busy={busy} onClick={() => void ask()}>
                  New conversation
                </Button>
              }
            >
              Each conversation is saved. Answers cite the passages they used, so you can open a source and check it yourself.
            </EmptyState>
          </div>
        )}
      </div>
    </div>
  );
}
