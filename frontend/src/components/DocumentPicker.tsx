import { useState } from "react";

import { useResource } from "../hooks/useResource";
import { useDebounced } from "../hooks/useTimers";
import { listDocuments } from "../services/documentService";
import { Dialog } from "./ui/Dialog";
import { Button, ErrorNotice, LoadingBlock, TextInput } from "./ui/primitives";

export type Selection = Record<string, string>; // document id -> name

interface Props {
  selected: Selection;
  onChange: (next: Selection) => void;
  disabled?: boolean;
}

/** Restricts one search or question to chosen documents (only searchable ones are offered). */
export default function DocumentPicker({ selected, onChange, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const search = useDebounced(text.trim());
  const list = useResource(
    (signal) => listDocuments({ page: 1, page_size: 50, status: "ready", search, sort: "filename", order: "asc" }, signal),
    [search],
    open,
  );
  const count = Object.keys(selected).length;

  const toggle = (id: string, name: string) => {
    const next = { ...selected };
    if (next[id]) delete next[id];
    else next[id] = name;
    onChange(next);
  };

  return (
    <>
      <Button size="sm" variant="ghost" icon="file" onClick={() => setOpen(true)} disabled={disabled} aria-haspopup="dialog">
        {count === 0 ? "All documents" : `${count} document${count > 1 ? "s" : ""} selected`}
      </Button>
      {count > 0 ? (
        <Button size="sm" variant="ghost" onClick={() => onChange({})} disabled={disabled}>
          Clear
        </Button>
      ) : null}
      <Dialog
        open={open}
        title="Choose documents"
        onClose={() => setOpen(false)}
        footer={
          <Button variant="primary" onClick={() => setOpen(false)}>
            Done
          </Button>
        }
      >
        <p className="muted">Only the chosen documents are used. This applies to this one request; nothing is remembered for the next.</p>
        <TextInput type="search" placeholder="Search by file name" aria-label="Search documents" value={text} onChange={(e) => setText(e.target.value)} />
        {list.error ? (
          <ErrorNotice error={list.error} onRetry={list.reload} compact />
        ) : !list.data ? (
          <LoadingBlock label="Loading documents" />
        ) : list.data.items.length === 0 ? (
          <p className="muted">No searchable documents{search ? " match" : " yet"}.</p>
        ) : (
          <ul className="check-list">
            {list.data.items.map((doc) => (
              <li key={doc.id}>
                <label>
                  <input type="checkbox" checked={Boolean(selected[doc.id])} onChange={() => toggle(doc.id, doc.filename)} />
                  <span>{doc.filename}</span>
                  <small className="muted">{doc.file_type.toUpperCase()}</small>
                </label>
              </li>
            ))}
          </ul>
        )}
        {list.data && list.data.page.total_items > list.data.items.length ? (
          <p className="muted small">Showing the first {list.data.items.length} of {list.data.page.total_items}; search to narrow the list.</p>
        ) : null}
      </Dialog>
    </>
  );
}
