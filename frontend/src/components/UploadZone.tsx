import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { Link } from "react-router-dom";

import { useMediaQuery } from "../hooks/useViewport";
import { ACCEPT, formatBytes, hasSupportedExtension } from "../lib/format";
import type { Reply } from "../services/apiClient";
import { ApiError, describeError, isAbort } from "../services/errors";
import { Icon } from "./ui/Icon";
import { IconButton, Spinner } from "./ui/primitives";

export interface UploadOutcome {
  /** Short confirmation shown on the finished row. */
  message: string;
  /** Where the user can look at what was created or matched. */
  link?: { to: string; label: string };
}

type Uploader = (
  file: File,
  handlers: { onProgress: (fraction: number) => void; signal: AbortSignal },
) => Promise<Reply<unknown>>;

interface Item {
  id: number;
  file: File;
  state: "waiting" | "uploading" | "done" | "error";
  progress: number;
  message?: string;
  link?: { to: string; label: string };
  tone?: "ok" | "info" | "error";
}

interface Props {
  upload: Uploader;
  /** Turns a successful reply into the message and link for the row. */
  describe: (file: File, reply: Reply<unknown>) => UploadOutcome;
  onFinished?: () => void;
  multiple?: boolean;
  compact?: boolean;
  label?: string;
}

let nextId = 1;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default function UploadZone({ upload, describe, onFinished, multiple = true, compact, label = "Upload documents" }: Props) {
  const [items, setItems] = useState<Item[]>([]);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const running = useRef(false);
  const queue = useRef<Item[]>([]);
  const abort = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const touch = useMediaQuery("(pointer: coarse)");

  useEffect(() => {
    mounted.current = true; // set again on the remount React StrictMode performs in development
    return () => {
      mounted.current = false;
      abort.current?.abort();
    };
  }, []);

  const patch = useCallback((id: number, change: Partial<Item>) => {
    if (mounted.current) setItems((all) => all.map((item) => (item.id === id ? { ...item, ...change } : item)));
  }, []);

  const pump = useCallback(async () => {
    if (running.current) return;
    running.current = true;
    while (queue.current.length > 0 && mounted.current) {
      const item = queue.current.shift() as Item;
      patch(item.id, { state: "uploading", progress: 0 });
      const controller = new AbortController();
      abort.current = controller;
      try {
        const reply = await upload(item.file, {
          onProgress: (fraction) => patch(item.id, { progress: fraction }),
          signal: controller.signal,
        });
        const outcome = describe(item.file, reply);
        patch(item.id, { state: "done", progress: 1, message: outcome.message, link: outcome.link, tone: "ok" });
        onFinished?.();
      } catch (error) {
        if (isAbort(error)) {
          patch(item.id, { state: "error", message: "Upload cancelled.", tone: "error" });
        } else if (error instanceof ApiError && error.code === "DUPLICATE_DOCUMENT") {
          const candidate = String(error.details.existing_document_id ?? "");
          const existing = UUID.test(candidate) ? candidate : ""; // an id from the server, still never put in a link unchecked
          patch(item.id, {
            state: "done",
            message: "An identical file is already in your library, so nothing new was added.",
            link: existing ? { to: `/documents/${existing}`, label: "Open the existing document" } : undefined,
            tone: "info",
          });
        } else {
          const info = describeError(error);
          patch(item.id, { state: "error", message: `${info.title}. ${info.message}`, tone: "error" });
        }
      }
    }
    running.current = false;
  }, [describe, onFinished, patch, upload]);

  const add = (files: FileList | File[]) => {
    const picked = Array.from(files).slice(0, multiple ? 20 : 1);
    const fresh: Item[] = picked.map((file) => {
      const supported = hasSupportedExtension(file.name);
      return {
        id: nextId++,
        file,
        state: supported ? "waiting" : "error",
        progress: 0,
        message: supported ? undefined : "Unsupported file type. Use PDF, DOCX, TXT or Markdown.",
        tone: supported ? undefined : "error",
      };
    });
    setItems((all) => [...fresh, ...all].slice(0, 12));
    queue.current.push(...fresh.filter((item) => item.state === "waiting"));
    void pump();
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    if (event.dataTransfer.files.length > 0) add(event.dataTransfer.files);
  };

  return (
    <div className="upload">
      <div
        className={`dropzone${dragging ? " dropzone-active" : ""}${compact ? " dropzone-compact" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <Icon name="upload" size={compact ? 20 : 24} />
        <div className="dropzone-text">
          <strong>{label}</strong>
          <span>
            {touch ? null : <>Drop {multiple ? "files" : "a file"} here, or </>}
            <button type="button" className={touch ? "btn btn-secondary btn-md" : "link-button"} onClick={() => input.current?.click()}>
              {touch ? `Choose ${multiple ? "files" : "a file"}` : "browse"}
            </button>
          </span>
          <span className="dropzone-rules">PDF, DOCX, TXT or Markdown. The server limits the size of a file (50 MB by default).</span>
        </div>
        <input
          ref={input}
          type="file"
          accept={ACCEPT}
          multiple={multiple}
          className="visually-hidden"
          aria-label={label}
          onChange={(event) => {
            if (event.target.files) add(event.target.files);
            event.target.value = "";
          }}
        />
      </div>
      {items.length > 0 ? (
        <ul className="upload-list" aria-live="polite">
          {items.map((item) => (
            <li key={item.id} className={`upload-item upload-${item.tone ?? item.state}`}>
              <div className="upload-name">
                <strong title={item.file.name}>{item.file.name}</strong>
                <span className="muted">{formatBytes(item.file.size)}</span>
              </div>
              <div className="upload-status">
                {item.state === "uploading" ? (
                  <>
                    <Spinner small />
                    <progress value={item.progress} max={1} aria-label={`Uploading ${item.file.name}`} />
                    <span>{item.progress >= 1 ? "Processing starts…" : `${Math.round(item.progress * 100)}%`}</span>
                  </>
                ) : item.state === "waiting" ? (
                  <span className="muted">Waiting…</span>
                ) : (
                  <span>
                    {item.message}{" "}
                    {item.link ? <Link to={item.link.to}>{item.link.label}</Link> : null}
                  </span>
                )}
              </div>
              {item.state === "uploading" ? (
                <IconButton icon="x" label={`Cancel upload of ${item.file.name}`} onClick={() => abort.current?.abort()} />
              ) : item.state !== "waiting" ? (
                <IconButton icon="x" label="Dismiss" onClick={() => setItems((all) => all.filter((x) => x.id !== item.id))} />
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
