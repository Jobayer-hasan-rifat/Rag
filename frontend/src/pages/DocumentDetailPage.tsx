import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { isBusy } from "../components/documentParts";
import { ConfirmDialog, Dialog } from "../components/ui/Dialog";
import { Icon } from "../components/ui/Icon";
import { Menu, MenuItem } from "../components/ui/Menu";
import { useToast } from "../components/ui/Toast";
import { Button, EmptyState, ErrorNotice, Field, LoadingBlock, StatusPill, TextInput, VersionTag } from "../components/ui/primitives";
import UploadZone from "../components/UploadZone";
import { useAskAbout } from "../hooks/useAskAbout";
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

type Tone = "ok" | "wait" | "bad" | "muted";

/** The state of one version in words a reader can act on; never "active" unless the API says so. */
function versionState(version: DocumentVersion): { label: string; tone: Tone; hint: string } {
  if (version.status === "failed") return { label: "Failed", tone: "bad", hint: "This version could not be processed. It is not used for search or answers." };
  if (isProcessing(version.status)) return { label: "Processing", tone: "wait", hint: "Not searchable yet. The active version keeps serving search until this one is ready." };
  if (version.is_active) return { label: "Active", tone: "ok", hint: "Search and answers use this version." };
  return { label: "Not active", tone: "muted", hint: "Ready, but search and answers are using another version." };
}

function StateBadge({ tone, label }: { tone: Tone; label: string }) {
  return (
    <span className={`pill pill-${tone}`}>
      {tone === "ok" ? <Icon name="check" size={13} /> : tone === "bad" ? <Icon name="alert" size={13} /> : tone === "wait" ? <span className="pill-spin" aria-hidden="true" /> : null}
      {label}
    </span>
  );
}

export default function DocumentDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const { ask, busy: asking } = useAskAbout();
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
      <EmptyState title="Document not found" action={<Link className="btn btn-secondary btn-md" to="/documents">Back to documents</Link>}>
        It may have been deleted, or it belongs to another account.
      </EmptyState>
    );
  }
  if (doc.error && !doc.data) return <ErrorNotice error={doc.error} onRetry={doc.reload} />;
  if (!doc.data) return <LoadingBlock label="Loading document" />;
  const d = doc.data;
  const list = versions.data ?? [];
  const activeVersion = list.find((version) => version.is_active);
  const shownIsActive = d.active_version_id !== null && d.active_version_id === d.version_id;
  const latest = d.latest_version ?? null;

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
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <Link to="/documents">Documents</Link>
        <Icon name="chevron" size={14} />
        <span aria-current="page">{d.filename}</span>
      </nav>

      <header className="doc-header">
        <span className="type-tile type-tile-lg" aria-hidden="true">
          {d.file_type.toUpperCase()}
        </span>
        <div className="doc-header-text">
          <h1 dir="auto">{d.filename}</h1>
          <p className="muted">
            {formatBytes(d.file_size)} · added {formatDateTime(d.created_at)}
          </p>
          <div className="doc-header-status">
            <StatusPill status={d.status} />
            <span className="muted small">{STATUS_HELP[d.status]}</span>
          </div>
        </div>
        <div className="doc-header-actions">
          <Button variant="primary" icon="chat" busy={asking} disabled={!d.active_version_id} onClick={() => void ask({ [d.id]: d.filename })}>
            Ask about this
          </Button>
          <Button icon="download" busy={downloading} onClick={() => void save()}>
            Download
          </Button>
          <Menu label="More document actions">
            <MenuItem
              icon="pencil"
              onSelect={() => {
                setName(d.filename);
                setRenameError(null);
                setRenaming(true);
              }}
            >
              Rename
            </MenuItem>
            <MenuItem icon="trash" danger onSelect={() => setDeleting("document")}>
              Delete document…
            </MenuItem>
          </Menu>
        </div>
      </header>

      <section className="panel callout" aria-label="What search and answers use">
        <h2 className="visually-hidden">What search and answers use</h2>
        <dl className="facts facts-wide">
          <div>
            <dt>Used for search and answers</dt>
            <dd>
              {d.active_version_id ? (
                <>
                  <VersionTag number={activeVersion?.version_number ?? (shownIsActive ? d.version_number : null)} tone="active" /> of {d.version_count}
                </>
              ) : (
                "No version yet"
              )}
            </dd>
            <dd className="muted small">{d.active_version_id ? "Only this version's passages are searched and cited." : "No version is searchable yet, so nothing can be searched or cited."}</dd>
          </div>
          <div>
            <dt>Version shown on this page</dt>
            <dd>
              <VersionTag number={d.version_number} tone={shownIsActive ? "active" : "muted"} /> {shownIsActive ? "active" : "not the active version"}
            </dd>
          </div>
          <div>
            <dt>Latest upload</dt>
            <dd>
              <VersionTag number={latest?.version_number ?? d.version_number} /> {latest ? latest.status : d.status}
            </dd>
            {latest?.status === "failed" ? <dd className="muted small">{failureText(latest.failure_reason)}</dd> : null}
          </div>
          <div>
            <dt>Passages</dt>
            <dd>{d.chunk_count ?? "–"}</dd>
            <dd className="muted small">{d.page_count ? `${d.page_count} pages` : "Page count not available"}</dd>
          </div>
        </dl>
      </section>

      <section className="panel" aria-labelledby="versions-heading">
        <div className="toolbar">
          <h2 id="versions-heading">Versions</h2>
        </div>
        <p className="muted measure">
          A new upload becomes a new version. The active version keeps serving search and answers until the new one has finished processing; a failed upload
          changes nothing. Older conversation answers keep pointing at the version they used, and are marked “earlier version” when it is no longer active.
        </p>
        <UploadZone
          compact
          multiple={false}
          label="Upload a new version"
          upload={(file, handlers) => uploadVersion(d.id, file, handlers)}
          describe={(file, reply) => {
            const result = reply.data as { created: boolean; version: DocumentVersion };
            return result.created
              ? { message: `“${file.name}” was uploaded as version ${result.version.version_number}. The active version stays active until it is ready.` }
              : { message: `This file is already version ${result.version.version_number} of this document, so no new version was created.` };
          }}
          onFinished={reloadAll}
        />
        {versions.error && !versions.data ? (
          <ErrorNotice error={versions.error} onRetry={versions.reload} />
        ) : !versions.data ? (
          <LoadingBlock label="Loading versions" />
        ) : (
          <ol className="versions" aria-label="Version history, newest first">
            {list.map((version) => {
              const state = versionState(version);
              return (
                <li key={version.id} className={`version${version.is_active ? " version-active" : ""}`}>
                  <div className="version-main">
                    <div className="version-title">
                      <VersionTag number={version.version_number} tone={version.is_active ? "active" : "muted"} />
                      <StateBadge tone={state.tone} label={state.label} />
                      {version.searchable ? <span className="muted small">Searchable</span> : <span className="muted small">Not searchable</span>}
                    </div>
                    <p className="version-name" dir="auto">
                      {version.filename}
                    </p>
                    <p className="muted small">
                      {formatDateTime(version.created_at)} · {formatBytes(version.file_size)}
                      {version.chunk_count !== null && version.chunk_count !== undefined ? ` · ${version.chunk_count} passages` : ""}
                    </p>
                    <p className="small version-hint">{state.hint}</p>
                    {version.status === "failed" ? <p className="small version-fail">{version.error_message ?? failureText(version.failure_reason)}</p> : null}
                  </div>
                  <div className="version-actions">
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
                      <Menu label={`More actions for version ${version.version_number}`}>
                        <MenuItem icon="trash" danger onSelect={() => setDeleting(version)}>
                          Delete version…
                        </MenuItem>
                      </Menu>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ol>
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
