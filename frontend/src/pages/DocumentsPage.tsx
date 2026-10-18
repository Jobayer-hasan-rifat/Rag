import { useState } from "react";
import { Link } from "react-router-dom";

import { DocumentStatus, isBusy, needsRetry, versionNote } from "../components/documentParts";
import { ConfirmDialog } from "../components/ui/Dialog";
import { useToast } from "../components/ui/Toast";
import { Button, EmptyState, ErrorNotice, LoadingBlock, PageHeader, Pagination, Select, TextInput } from "../components/ui/primitives";
import UploadZone from "../components/UploadZone";
import { useResource } from "../hooks/useResource";
import { useDebounced, usePolling } from "../hooks/useTimers";
import { formatBytes, formatDate, STATUS_LABEL } from "../lib/format";
import { deleteDocument, listDocuments, retryDocument, uploadDocument } from "../services/documentService";
import type { DocumentItem, DocumentStatus as Status, SortField } from "../types/api";

const STATUSES: Status[] = ["ready", "failed", "pending", "parsing", "chunking", "chunked", "embedding"];
const SORTS: { value: string; label: string; sort: SortField; order: "asc" | "desc" }[] = [
  { value: "new", label: "Newest first", sort: "created_at", order: "desc" },
  { value: "old", label: "Oldest first", sort: "created_at", order: "asc" },
  { value: "name", label: "Name A to Z", sort: "filename", order: "asc" },
  { value: "size", label: "Largest first", sort: "file_size", order: "desc" },
];

export default function DocumentsPage() {
  const toast = useToast();
  const [page, setPage] = useState(1);
  const [text, setText] = useState("");
  const search = useDebounced(text.trim());
  const [status, setStatus] = useState<Status | "">("");
  const [type, setType] = useState("");
  const [sortKey, setSortKey] = useState("new");
  const [doomed, setDoomed] = useState<DocumentItem | null>(null);
  const [retrying, setRetrying] = useState<string | null>(null);
  const sort = SORTS.find((entry) => entry.value === sortKey) ?? SORTS[0];

  const list = useResource(
    (signal) =>
      listDocuments({ page, page_size: 20, status, file_type: type, search, sort: sort.sort, order: sort.order }, signal),
    [page, status, type, search, sort.sort, sort.order],
  );
  const items = list.data?.items ?? [];
  usePolling(list.reload, 4000, items.some(isBusy));

  const filtered = Boolean(search || status || type);

  const retry = async (doc: DocumentItem) => {
    setRetrying(doc.id);
    try {
      await retryDocument(doc.id);
      toast.success(`Processing of “${doc.filename}” was queued again.`);
      list.reload();
    } catch (error) {
      toast.error(error);
    } finally {
      setRetrying(null);
    }
  };

  const confirmDelete = async () => {
    if (!doomed) return;
    await deleteDocument(doomed.id);
    toast.success(`“${doomed.filename}” was deleted.`);
    setDoomed(null);
    if (items.length === 1 && page > 1) setPage(page - 1);
    else list.reload();
  };

  return (
    <>
      <PageHeader title="Documents" subtitle="Upload files, follow their processing and manage versions." />
      <UploadZone
        upload={(file, handlers) => uploadDocument(file, handlers)}
        describe={(file) => ({ message: `“${file.name}” was uploaded and is being processed. It is not searchable until processing finishes.` })}
        onFinished={() => {
          setPage(1);
          list.reload();
        }}
      />

      <section className="panel" aria-labelledby="library-heading">
        <div className="toolbar">
          <h2 id="library-heading">Library</h2>
          <div className="filters">
            <TextInput type="search" placeholder="Search by file name" aria-label="Search by file name" value={text} onChange={(e) => { setText(e.target.value); setPage(1); }} />
            <Select aria-label="Filter by status" value={status} onChange={(e) => { setStatus(e.target.value as Status | ""); setPage(1); }}>
              <option value="">Any status</option>
              {STATUSES.map((value) => (
                <option key={value} value={value}>
                  {STATUS_LABEL[value]}
                </option>
              ))}
            </Select>
            <Select aria-label="Filter by file type" value={type} onChange={(e) => { setType(e.target.value); setPage(1); }}>
              <option value="">Any type</option>
              {["pdf", "docx", "txt", "md"].map((value) => (
                <option key={value} value={value}>
                  {value.toUpperCase()}
                </option>
              ))}
            </Select>
            <Select aria-label="Sort" value={sortKey} onChange={(e) => { setSortKey(e.target.value); setPage(1); }}>
              {SORTS.map((entry) => (
                <option key={entry.value} value={entry.value}>
                  {entry.label}
                </option>
              ))}
            </Select>
          </div>
        </div>

        {list.error && !list.data ? (
          <ErrorNotice error={list.error} onRetry={list.reload} />
        ) : !list.data ? (
          <LoadingBlock label="Loading documents" />
        ) : items.length === 0 ? (
          <EmptyState title={filtered ? "No documents match" : "No documents yet"}>
            {filtered ? "Try a different search or clear the filters." : "Upload a PDF, Word, text or Markdown file above to get started."}
          </EmptyState>
        ) : (
          <>
            {list.error ? <ErrorNotice error={list.error} onRetry={list.reload} compact /> : null}
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Status</th>
                  <th scope="col">Size</th>
                  <th scope="col">Added</th>
                  <th scope="col">
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody aria-busy={list.loading || undefined}>
                {items.map((doc) => (
                  <tr key={doc.id}>
                    <td data-label="Name">
                      <Link to={`/documents/${doc.id}`} className="doc-name">
                        {doc.filename}
                      </Link>
                      <div className="muted small">
                        {doc.file_type.toUpperCase()} · {versionNote(doc)}
                      </div>
                    </td>
                    <td data-label="Status">
                      <DocumentStatus doc={doc} />
                    </td>
                    <td data-label="Size">{formatBytes(doc.file_size)}</td>
                    <td data-label="Added">{formatDate(doc.created_at)}</td>
                    <td className="row-actions">
                      {needsRetry(doc) ? (
                        <Button size="sm" icon="retry" busy={retrying === doc.id} onClick={() => void retry(doc)}>
                          Retry
                        </Button>
                      ) : null}
                      <Link className="btn btn-secondary btn-sm" to={`/documents/${doc.id}`}>
                        Open
                      </Link>
                      <Button size="sm" variant="ghost" icon="trash" aria-label={`Delete ${doc.filename}`} onClick={() => setDoomed(doc)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination page={list.data.page.page} totalPages={list.data.page.total_pages} totalItems={list.data.page.total_items} onPage={setPage} noun="documents" />
          </>
        )}
      </section>

      <ConfirmDialog
        open={doomed !== null}
        title="Delete this document?"
        confirmLabel="Delete document"
        danger
        onConfirm={confirmDelete}
        onClose={() => setDoomed(null)}
      >
        <p>
          <strong>{doomed?.filename}</strong> and all {doomed?.version_count ?? 1} of its version(s), their stored files and
          search index are removed permanently.
        </p>
        <p>Earlier conversation answers are kept, but their citations to this document will show only that the source was deleted.</p>
      </ConfirmDialog>
    </>
  );
}
