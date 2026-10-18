import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { useResizableWidth } from "../hooks/useResizable";
import { useMediaQuery } from "../hooks/useViewport";
import { clearPending, loadPending, savePending, type PendingQuestion } from "../lib/pending";
import { ApiError, isAbort } from "../services/errors";
import { deleteConversation, getConversation, newIdempotencyKey, renameConversation, sendMessage } from "../services/conversationService";
import type { Conversation, Message, MessageCitation } from "../types/api";
import DocumentPicker, { type Selection } from "./DocumentPicker";
import { AssistantMessage, QuestionMessage } from "./MessageView";
import SourcePanel, { sourceFromCitation, type SourceView } from "./SourcePanel";
import { useUploadDialog } from "./UploadDialog";
import { ConfirmDialog, Dialog } from "./ui/Dialog";
import { Icon } from "./ui/Icon";
import { Menu, MenuItem } from "./ui/Menu";
import { useToast } from "./ui/Toast";
import { Button, EmptyState, ErrorNotice, Field, LoadingBlock, Notice, Switch, TextInput } from "./ui/primitives";
import { useResource } from "../hooks/useResource";
import { usePolling } from "../hooks/useTimers";
import { listDocuments } from "../services/documentService";

interface Props {
  conversationId: string;
  onListChange: () => void;
  historyOpen: boolean;
  onToggleHistory: () => void;
}

function merge(current: Message[], incoming: Message[]): Message[] {
  const byId = new Map(current.map((message) => [message.id, message]));
  for (const message of incoming) byId.set(message.id, message);
  return [...byId.values()].sort((a, b) => a.sequence - b.sequence);
}

export default function Thread({ conversationId, onListChange, historyOpen, onToggleHistory }: Props) {
  const navigate = useNavigate();
  const location = useLocation();
  const upload = useUploadDialog();
  const docked = useMediaQuery("(min-width: 1180px)");
  const touch = useMediaQuery("(pointer: coarse)");
  const panel = useResizableWidth("rag.ui.evidence.width", 400, 300, 640);
  const readyDocs = useResource((signal) => listDocuments({ page: 1, page_size: 1, status: "ready" }, signal), []);
  const field = useRef<HTMLTextAreaElement>(null);
  const scope = (location.state as { scope?: Selection } | null)?.scope;
  const toast = useToast();
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [pagesLoaded, setPagesLoaded] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error" | "missing">("loading");
  const [loadError, setLoadError] = useState<unknown>(null);
  const [loadingEarlier, setLoadingEarlier] = useState(false);

  const [text, setText] = useState("");
  const [docs, setDocs] = useState<Selection>({});
  const [rerank, setRerank] = useState(false);
  const [rerankOff, setRerankOff] = useState(false);
  const [pending, setPending] = useState<PendingQuestion | null>(null);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<unknown>(null);

  const [source, setSource] = useState<SourceView | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameError, setRenameError] = useState<unknown>(null);
  const [deleting, setDeleting] = useState(false);

  const bottom = useRef<HTMLDivElement>(null);
  const alive = useRef(true);
  const current = useRef(conversationId);
  current.current = conversationId;
  useEffect(() => {
    alive.current = true; // set again on the remount React StrictMode performs in development
    return () => {
      alive.current = false;
    };
  }, []);

  /** (Re)load the newest page and merge it, so the screen always agrees with the server. */
  const reconcile = useCallback(async () => {
    const id = conversationId;
    try {
      const detail = await getConversation(id, 1);
      if (!alive.current || current.current !== id) return;
      setConversation(detail.conversation);
      setMessages((all) => merge(all, detail.messages));
      setTotalPages(detail.page.total_pages);
      setLoadState("ready");
    } catch (error) {
      if (!alive.current || current.current !== id || isAbort(error)) return;
      if (error instanceof ApiError && error.status === 404) setLoadState("missing");
      else {
        setLoadError(error);
        setLoadState((state) => (state === "ready" ? state : "error"));
      }
    }
  }, [conversationId]);

  useEffect(() => {
    setConversation(null);
    setMessages([]);
    setPagesLoaded(1);
    setTotalPages(1);
    setLoadState("loading");
    setText("");
    setDocs(scope ?? {});
    setSendError(null);
    setSending(false);
    setSource(null);
    setPending(loadPending(conversationId));
    void reconcile();
    // the scope only seeds a newly opened conversation
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, reconcile]);

  // grow the field with its text, up to the height the stylesheet allows
  useEffect(() => {
    const element = field.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
  }, [text]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages.length, sending]);

  const unanswered = messages.some((message) => message.role === "user" && message.status === "processing");
  usePolling(() => void reconcile(), 3000, unanswered && !sending);

  const send = useCallback(
    async (record: PendingQuestion) => {
      setSending(true);
      setSendError(null);
      savePending(conversationId, record);
      setPending(record);
      try {
        const reply = await sendMessage(
          conversationId,
          {
            content: record.content,
            ...(record.documentIds.length > 0 ? { document_ids: record.documentIds } : {}),
            ...(record.rerank ? { rerank: true } : {}),
          },
          record.key,
        );
        if (!alive.current || current.current !== conversationId) return;
        clearPending(conversationId);
        setPending(null);
        setMessages((all) => merge(all, [reply.user_message, reply.assistant_message]));
        setConversation(reply.conversation);
        onListChange();
      } catch (error) {
        if (!alive.current || current.current !== conversationId) return;
        if (error instanceof ApiError && error.code === "RERANKING_DISABLED") {
          setRerankOff(true);
          setRerank(false);
        }
        setSendError(error);
        await reconcile(); // the server may have kept the question: show what it actually has
      } finally {
        if (alive.current && current.current === conversationId) setSending(false);
      }
    },
    [conversationId, onListChange, reconcile],
  );

  const submit = () => {
    const content = text.trim();
    if (!content || sending) return;
    const record: PendingQuestion = {
      key: newIdempotencyKey(),
      content,
      documentIds: Object.keys(docs),
      rerank: rerank && !rerankOff,
      startedAt: Date.now(),
    };
    setText("");
    setDocs({}); // a filter belongs to one question
    void send(record);
  };

  const onKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && !touch) {
      event.preventDefault();
      submit();
    }
  };

  // The unanswered question in the thread that the stored record belongs to (same text, failed).
  const lastQuestion = [...messages].reverse().find((message) => message.role === "user");
  const retryable =
    pending && lastQuestion && lastQuestion.status === "failed" && lastQuestion.content === pending.content ? pending : null;
  const notSent = pending && !sending && loadState === "ready" && !retryable && !(lastQuestion && lastQuestion.content === pending.content) ? pending : null;

  const retryFailed = (message: Message) => {
    if (retryable) void send(retryable);
    else {
      // no stored key for this question (for example after a restart): ask it again as a new question
      void send({ key: newIdempotencyKey(), content: message.content, documentIds: [], rerank: false, startedAt: Date.now() });
    }
  };

  const loadEarlier = async () => {
    setLoadingEarlier(true);
    try {
      const detail = await getConversation(conversationId, pagesLoaded + 1);
      setMessages((all) => merge(all, detail.messages));
      setPagesLoaded((value) => value + 1);
      setTotalPages(detail.page.total_pages);
    } catch (error) {
      toast.error(error);
    } finally {
      setLoadingEarlier(false);
    }
  };

  const openSource = (citation: MessageCitation) => setSource(sourceFromCitation(citation));

  if (loadState === "missing") {
    return (
      <EmptyState title="Conversation not found" action={<Link className="btn btn-secondary" to="/workspace">Back to conversations</Link>}>
        It may have been deleted.
      </EmptyState>
    );
  }
  if (loadState === "error" && messages.length === 0) return <ErrorNotice error={loadError} onRetry={() => void reconcile()} />;
  if (loadState === "loading" && !conversation) return <LoadingBlock label="Opening conversation" />;

  const scopeNames = Object.values(docs);
  const nothingSearchable = readyDocs.data !== undefined && readyDocs.data.page.total_items === 0;
  const activeLabel = source?.label;

  return (
    <div className="thread-layout">
      <section className="thread" aria-label="Conversation">
        <header className="thread-head">
          <Link to="/workspace" className="icon-btn thread-back" aria-label="Back to conversations">
            <Icon name="back" />
          </Link>
          <button
            type="button"
            className="icon-btn thread-history"
            onClick={onToggleHistory}
            aria-pressed={historyOpen}
            aria-label={historyOpen ? "Hide conversation list" : "Show conversation list"}
            title={historyOpen ? "Hide conversation list" : "Show conversation list"}
          >
            <Icon name="panel" />
          </button>
          <h2 title={conversation?.title}>{conversation?.title ?? "Conversation"}</h2>
          <Menu label="Conversation actions">
            <MenuItem
              icon="pencil"
              onSelect={() => {
                setTitle(conversation?.title ?? "");
                setRenameError(null);
                setRenaming(true);
              }}
            >
              Rename
            </MenuItem>
            <MenuItem icon="trash" danger onSelect={() => setDeleting(true)}>
              Delete
            </MenuItem>
          </Menu>
        </header>

        <div className="thread-scroll" role="log" aria-label="Messages" aria-live="polite">
          <div className="thread-inner">
            {pagesLoaded < totalPages ? (
              <div className="load-earlier">
                <Button size="sm" busy={loadingEarlier} onClick={() => void loadEarlier()}>
                  Load earlier messages
                </Button>
              </div>
            ) : null}
            {messages.length === 0 && !sending ? (
              <div className="thread-empty">
                <h2>Ask about your documents</h2>
                <p>Answers are written only from passages in your searchable documents, and every claim points to the passage it came from so you can check it.</p>
                {nothingSearchable ? (
                  <Notice tone="warn">
                    None of your documents is searchable yet. Add a document and wait until it shows as Ready; then ask your question.{" "}
                    <button type="button" className="link-button" onClick={upload.open}>
                      Add documents
                    </button>
                  </Notice>
                ) : (
                  <ul>
                    <li>Be specific: name the topic, the person or the section you care about.</li>
                    <li>Choose documents below to limit this one question to particular files.</li>
                    <li>Open a source number in the answer to read the original passage.</li>
                  </ul>
                )}
              </div>
            ) : null}
            {messages.map((message) =>
              message.role === "user" ? (
                <QuestionMessage
                  key={message.id}
                  message={message}
                  onRetry={message.status === "failed" && message.id === lastQuestion?.id && !sending ? () => retryFailed(message) : undefined}
                  retryLabel={retryable ? "Retry" : "Ask again"}
                  retrying={sending}
                />
              ) : (
                <AssistantMessage key={message.id} message={message} onOpenSource={openSource} activeLabel={activeLabel} />
              ),
            )}
            {sending && pending ? (
              <>
                {!messages.some((m) => m.role === "user" && m.content === pending.content && m.status !== "failed") ? (
                  <article className="entry entry-question entry-sending" aria-label="Your question (sending)">
                    <div className="question-text" dir="auto">
                      {pending.content.split("\n").map((line, index) => (
                        <p key={index}>{line}</p>
                      ))}
                    </div>
                  </article>
                ) : null}
                <p className="working" role="status">
                  <span className="dots" aria-hidden="true" /> Searching your documents and writing an answer…
                </p>
              </>
            ) : null}
            <div ref={bottom} />
          </div>
        </div>

        <div className="composer-wrap">
          <div className="composer-inner">
            {sendError || notSent ? (
              <div className="composer-error">
                {sendError ? (
                  <ErrorNotice error={sendError} onRetry={notSent ? () => void send(notSent) : undefined} compact />
                ) : (
                  <Notice tone="warn">
                    Your last question was not sent: “{notSent?.content}”
                    <span className="notice-actions">
                      <Button size="sm" variant="primary" onClick={() => notSent && void send(notSent)}>
                        Send it now
                      </Button>
                    </span>
                  </Notice>
                )}
                {notSent ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setText(notSent.content);
                      clearPending(conversationId);
                      setPending(null);
                      setSendError(null);
                    }}
                  >
                    Edit the question instead
                  </Button>
                ) : null}
              </div>
            ) : null}
            {rerankOff ? <Notice tone="warn">Reranking is not enabled on this server, so it is switched off.</Notice> : null}
            <form
              className="composer"
              onSubmit={(event) => {
                event.preventDefault();
                submit();
              }}
            >
              <label className="visually-hidden" htmlFor="composer-input">
                Your question
              </label>
              <textarea
                ref={field}
                id="composer-input"
                className="composer-input"
                value={text}
                onChange={(event) => setText(event.target.value)}
                onKeyDown={onKey}
                placeholder="Ask a question about your documents…"
                rows={1}
                maxLength={4000}
                disabled={sending}
                enterKeyHint={touch ? "enter" : "send"}
              />
              <div className="composer-bar">
                <div className="composer-tools">
                  <button type="button" className="btn btn-ghost btn-sm" onClick={upload.open} aria-label="Add documents to your library">
                    <Icon name="paperclip" size={16} />
                    <span className="tool-label">Add</span>
                  </button>
                  <DocumentPicker selected={docs} onChange={setDocs} disabled={sending} />
                  <Switch label="Rerank" checked={rerank} disabled={rerankOff || sending} onChange={setRerank} hint="Reorder the best passages with a second model. Slower; can improve the order." />
                </div>
                <button type="submit" className="send-btn" disabled={!text.trim() || sending} aria-label="Ask">
                  {sending ? <span className="spinner spinner-sm spinner-light" aria-hidden="true"><span className="spinner-ring" /></span> : <Icon name="arrowUp" size={20} />}
                </button>
              </div>
            </form>
            <p className="composer-hint">
              {scopeNames.length > 0 ? <>This question uses only: {scopeNames.join(", ")}. </> : null}
              {touch ? "Tap the arrow to send." : "Enter to send, Shift+Enter for a new line."}
            </p>
          </div>
        </div>
      </section>

      <SourcePanel source={source} onClose={() => setSource(null)} docked={docked ? { width: panel.width, handle: panel.handle } : undefined} />

      <Dialog
        open={renaming}
        title="Rename conversation"
        onClose={() => setRenaming(false)}
        footer={
          <>
            <Button onClick={() => setRenaming(false)} disabled={renameBusy}>
              Cancel
            </Button>
            <Button
              variant="primary"
              busy={renameBusy}
              disabled={!title.trim()}
              onClick={async () => {
                setRenameBusy(true);
                setRenameError(null);
                try {
                  setConversation(await renameConversation(conversationId, title.trim()));
                  setRenaming(false);
                  onListChange();
                } catch (error) {
                  setRenameError(error);
                } finally {
                  setRenameBusy(false);
                }
              }}
            >
              Save
            </Button>
          </>
        }
      >
        <Field label="Title">{(props) => <TextInput {...props} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} autoFocus />}</Field>
        {renameError ? <ErrorNotice error={renameError} compact /> : null}
      </Dialog>

      <ConfirmDialog
        open={deleting}
        title="Delete this conversation?"
        confirmLabel="Delete conversation"
        danger
        onConfirm={async () => {
          await deleteConversation(conversationId);
          clearPending(conversationId);
          toast.success("Conversation deleted.");
          setDeleting(false);
          onListChange();
          navigate("/workspace", { replace: true });
        }}
        onClose={() => setDeleting(false)}
      >
        <p>The conversation and all of its questions and answers are removed permanently. Your documents are not affected.</p>
      </ConfirmDialog>
    </div>
  );
}
