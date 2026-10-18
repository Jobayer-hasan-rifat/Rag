import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { isBusy } from "../components/documentParts";
import { ConfirmDialog, Dialog } from "../components/ui/Dialog";
import { useToast } from "../components/ui/Toast";
import { Button, EmptyState, ErrorNotice, Field, LoadingBlock, StatusPill, TextInput, VersionTag } from "../components/ui/primitives";
import UploadZone from "../components/UploadZone";
import { useResource } from "../hooks/useResource";
import { usePolling } from "../hooks/useTimers";
import { failureText, formatBytes, formatDateTime, isProcessing, STATUS_HELP } from "../lib/format";
import {
  activateVersion,
  deleteDocument,
  deleteVersion,
  downloadDocument,
  getDocument,
  listVersions,
  renameDocument,
  retryVersion,
  uploadVersion,
} from "../services/documentService";
import { ApiError } from "../services/errors";
import type { DocumentVersion } from "../types/api";

function versionState(version: DocumentVersion): { label: string; tone: "ok" | "wait" | "bad" | "muted" } {
  if (version.status === "failed") return { label: "Failed", tone: "bad" };
  if (isProcessing(version.status)) return { label: "Processing", tone: "wait" };
  if (version.is_active) return { label: "Active", tone: "ok" };
  return { label: "Superseded", tone: "muted" };
}

export default function DocumentDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const doc = useResource((signal) => getDocument(id, signal), [id]);
  const versions = useResource((signal) => listVersions(id, signal), [id]);
  const [busyVersion, setBusyVersion] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<DocumentVersion | "document" | null>(null);
  const [restoring, setRestoring] = useState<DocumentVersion | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState("");
  const [renameError, setRenameError] = useState<unknown>(null);
  const [renameBusy, setRenameBusy] = useState(false);
  const [downloading, setDownloading] = useState(false);

  const reloadAll = () => {
    doc.reload();
    versions.reload();
  };
  const busy = (doc.data ? isBusy(doc.data) : false) || (versions.data ?? []).some((v) => isProcessing(v.status));
  usePolling(reloadAll, 4000, busy);

  if (doc.error instanceof ApiError && doc.error.status === 404) {
    return (
      <EmptyState title="Document not found" action={<Link className="btn btn-secondary" to="/documents">Back to documents</Link>}>
        It may have been deleted, or it belongs to another account.
      </EmptyState>
    );
  }
  if (doc.error && !doc.data) return <ErrorNotice error={doc.error} onRetry={doc.reload} />;
  if (!doc.data) return <LoadingBlock label="Loading document" />;
  const d = doc.data;
  const list = versions.data ?? [];

  const act = async (version: DocumentVersion, run: () => Promise<unknown>, done: string) => {
    setBusyVersion(version.id);
    try {
      await run();
      toast.success(done);
      reloadAll();
    } catch (error) {
      toast.error(error);
    } finally {
      setBusyVersion(null);
    }
  };

  const submitRename = async () => {
    setRenameBusy(true);
    setRenameError(null);
    try {
      await renameDocument(d.id, name.trim());
      setRenaming(false);
      toast.success("Document renamed.");
      reloadAll();
    } catch (error) {
      setRenameError(error);
    } finally {
      setRenameBusy(false);
    }
  };

  const save = async () => {
    setDownloading(true);
    try {
      await downloadDocument(d.id, d.filename);
    } catch (error) {
      toast.error(error);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <>
      <p className="breadcrumb">
        <Link to="/documents">Documents</Link> / <span>{d.filename}</span>
      </p>
      <header className="page-header">
        <div>
          <h1>{d.filename}</h1>
          <p className="muted">
            {d.file_type.toUpperCase()} · {formatBytes(d.file_size)} · added {formatDateTime(d.created_at)}
          </p>
        </div>
        <div className="page-actions">
          <Button icon="download" busy={downloading} onClick={() => void save()}>
            Download current
          </Button>
          <Button icon="pencil" onClick={() => { setName(d.filename); setRenameError(null); setRenaming(true); }}>
            Rename
          </Button>
          <Button variant="danger" icon="trash" onClick={() => setDeleting("document")}>
            Delete
          </Button>
        </div>
      </header>

      <section className="panel summary-grid" aria-label="Summary">
        <div>
          <span className="label">Status</span>
          <StatusPill status={d.status} />
          <p className="muted small">{STATUS_HELP[d.status]}</p>
        </div>
        <div>
          <span className="label">Shown version</span>
          <p>
            <VersionTag number={d.version_number} tone={d.active_version_id === d.version_id ? "active" : "muted"} />{" "}
            of {d.version_count}
          </p>
          <p className="muted small">{d.active_version_id ? "This is the version search and answers use." : "No version is searchable yet."}</p>
        </div>
        <div>
          <span className="label">Latest version</span>
          <p>
            <VersionTag number={d.latest_version?.version_number ?? d.version_number} />{" "}
            {d.latest_version ? d.latest_version.status : d.status}
          </p>
          {d.latest_version?.status === "failed" ? <p className="muted small">{failureText(d.latest_version.failure_reason)}</p> : null}
        </div>
        <div>
          <span className="label">Passages</span>
          <p>{d.chunk_count ?? "-"}</p>
          <p className="muted small">{d.page_count ? `${d.page_count} pages` : "Page count not available"}</p>
        </div>
      </section>

      <section className="panel" aria-labelledby="versions-heading">
        <div className="toolbar">
          <h2 id="versions-heading">Versions</h2>
        </div>
        <p className="muted">
          A new upload becomes a new version. The current version keeps serving search and answers until the new one has
          finished processing; a failed upload changes nothing. Older versions are kept until you delete them.
        </p>
        <UploadZone
          compact
          multiple={false}
          label="Upload a new version"
          upload={(file, handlers) => uploadVersion(d.id, file, handlers)}
          describe={(file, reply) => {
            const result = reply.data as { created: boolean; version: DocumentVersion };
            return result.created
              ? { message: `“${file.name}” was uploaded as version ${result.version.version_number}. The current version stays active until it is ready.` }
              : { message: `This file is already version ${result.version.version_number} of this document, so no new version was created.` };
          }}
          onFinished={reloadAll}
        />
        {versions.error && !versions.data ? (
          <ErrorNotice error={versions.error} onRetry={versions.reload} />
        ) : !versions.data ? (
          <LoadingBlock label="Loading versions" />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Version</th>
                <th scope="col">State</th>
                <th scope="col">Added</th>
                <th scope="col">Size</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {list.map((version) => {
                const state = versionState(version);
                return (
                  <tr key={version.id}>
                    <td data-label="Version">
                      <VersionTag number={version.version_number} tone={version.is_active ? "active" : "muted"} />
                      <div className="muted small">{version.filename}</div>
                    </td>
                    <td data-label="State">
                      <span className={`pill pill-${state.tone}`}>
                        <span className="pill-dot" aria-hidden="true" />
                        {state.label}
                      </span>
                      <details className="version-details">
                        <summary>Details</summary>
                        <dl>
                          <dt>Status</dt>
                          <dd>{version.status}</dd>
                          {version.status === "failed" ? (
                            <>
                              <dt>Why it failed</dt>
                              <dd>{version.error_message ?? failureText(version.failure_reason)}</dd>
                            </>
                          ) : null}
                          <dt>Passages</dt>
                          <dd>{version.chunk_count ?? "-"}</dd>
                          <dt>Processed</dt>
                          <dd>{version.processing_completed_at ? formatDateTime(version.processing_completed_at) : "-"}</dd>
                          <dt>Searchable</dt>
                          <dd>{version.searchable ? "Yes" : "No"}</dd>
                        </dl>
                      </details>
                    </td>
                    <td data-label="Added">{formatDateTime(version.created_at)}</td>
                    <td data-label="Size">{formatBytes(version.file_size)}</td>
                    <td className="row-actions">
                      {version.status === "failed" ? (
                        <Button size="sm" icon="retry" busy={busyVersion === version.id} onClick={() => void act(version, () => retryVersion(d.id, version.id), `Processing of version ${version.version_number} was queued again.`)}>
                          Retry
                        </Button>
                      ) : null}
                      {version.status === "ready" && !version.is_active ? (
                        <Button size="sm" icon="restore" busy={busyVersion === version.id} onClick={() => setRestoring(version)}>
                          Make active
                        </Button>
                      ) : null}
                      {!version.is_active && list.length > 1 ? (
                        <Button size="sm" variant="ghost" icon="trash" aria-label={`Delete version ${version.version_number}`} onClick={() => setDeleting(version)} />
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      <ConfirmDialog
        open={restoring !== null}
        title={`Make version ${restoring?.version_number} active?`}
        confirmLabel="Make active"
        onConfirm={async () => {
          if (!restoring) return;
          await activateVersion(d.id, restoring.id);
          toast.success(`Version ${restoring.version_number} is now the active version.`);
          setRestoring(null);
          reloadAll();
        }}
        onClose={() => setRestoring(null)}
      >
        <p>Search and answers will use this version instead of the current one. The current version is kept.</p>
        <p>A version that finishes processing later and is newer than this one will become active automatically.</p>
      </ConfirmDialog>

      <ConfirmDialog
        open={deleting !== null}
        title={deleting === "document" ? "Delete this document?" : `Delete version ${deleting?.version_number}?`}
        confirmLabel={deleting === "document" ? "Delete document" : "Delete version"}
        danger
        onConfirm={async () => {
          if (deleting === "document") {
            await deleteDocument(d.id);
            toast.success("Document deleted.");
            navigate("/documents", { replace: true });
          } else if (deleting) {
            await deleteVersion(d.id, deleting.id);
            toast.success(`Version ${deleting.version_number} was deleted.`);
            setDeleting(null);
            reloadAll();
          }
        }}
        onClose={() => setDeleting(null)}
      >
        {deleting === "document" ? (
          <p>
            All {d.version_count} version(s), their stored files and search index are removed permanently. Earlier
            conversation answers are kept, but their citations to this document will show only that the source was deleted.
          </p>
        ) : (
          <p>
            This version, its stored file and its search index are removed permanently. Earlier conversation answers that
            cited it are kept, but those citations will show only that the source was deleted.
          </p>
        )}
      </ConfirmDialog>

      <Dialog
        open={renaming}
        title="Rename document"
        onClose={() => setRenaming(false)}
        footer={
          <>
            <Button onClick={() => setRenaming(false)} disabled={renameBusy}>
              Cancel
            </Button>
            <Button variant="primary" busy={renameBusy} disabled={!name.trim() || name.trim() === d.filename} onClick={() => void submitRename()}>
              Save
            </Button>
          </>
        }
      >
        <Field label="File name" hint="The extension must match the file type.">
          {(props) => <TextInput {...props} value={name} onChange={(e) => setName(e.target.value)} maxLength={255} autoFocus />}
        </Field>
        {renameError ? <ErrorNotice error={renameError} compact /> : null}
      </Dialog>
    </>
  );
}
