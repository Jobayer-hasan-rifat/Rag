import { useId, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

import { describeError } from "../../services/errors";
import type { DocumentStatus } from "../../types/api";
import { isProcessing, STATUS_HELP, STATUS_LABEL } from "../../lib/format";
import { Icon, type IconName } from "./Icon";

// --- buttons ----------------------------------------------------------------------------------

type Variant = "primary" | "secondary" | "ghost" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  busy?: boolean;
  icon?: IconName;
  size?: "md" | "sm";
}

export function Button({ variant = "secondary", busy, icon, size = "md", children, disabled, className, ...rest }: ButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      className={`btn btn-${variant} btn-${size}${className ? ` ${className}` : ""}`}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
    >
      {busy ? <Spinner small /> : icon ? <Icon name={icon} size={16} /> : null}
      {children ? <span>{children}</span> : null}
    </button>
  );
}

export function IconButton({ icon, label, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { icon: IconName; label: string }) {
  return (
    <button type="button" {...rest} className={`icon-btn${rest.className ? ` ${rest.className}` : ""}`} aria-label={label} title={label}>
      <Icon name={icon} />
    </button>
  );
}

// --- feedback ---------------------------------------------------------------------------------

export function Spinner({ small, label }: { small?: boolean; label?: string }) {
  return (
    <span className={`spinner${small ? " spinner-sm" : ""}`} role={label ? "status" : undefined} aria-label={label}>
      <span className="spinner-ring" aria-hidden="true" />
    </span>
  );
}

export function Skeleton({ lines = 3, height = 14 }: { lines?: number; height?: number }) {
  return (
    <div className="skeleton" aria-hidden="true">
      {Array.from({ length: lines }, (_, index) => (
        <span key={index} className="skeleton-line" style={{ height, width: `${100 - ((index * 13) % 40)}%` }} />
      ))}
    </div>
  );
}

export function LoadingBlock({ label = "Loading" }: { label?: string }) {
  return (
    <div className="loading-block" role="status">
      <Spinner />
      <span>{label}…</span>
    </div>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <h3>{title}</h3>
      {children ? <p>{children}</p> : null}
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  );
}

/** An error with a plain explanation and, when repeating can help, a retry action. */
export function ErrorNotice({ error, onRetry, compact }: { error: unknown; onRetry?: () => void; compact?: boolean }) {
  const info = describeError(error);
  return (
    <div className={`notice notice-error${compact ? " notice-compact" : ""}`} role="alert">
      <Icon name="alert" />
      <div className="notice-body">
        <strong>{info.title}</strong>
        <span>{info.message}</span>
      </div>
      {onRetry && info.retryable ? (
        <Button size="sm" variant="secondary" onClick={onRetry} icon="retry">
          Try again
        </Button>
      ) : null}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "ok"; children: ReactNode }) {
  return (
    <div className={`notice notice-${tone}`} role={tone === "warn" ? "alert" : "status"}>
      <div className="notice-body">{children}</div>
    </div>
  );
}

// --- status ------------------------------------------------------------------------------------

export function StatusPill({ status }: { status: DocumentStatus }) {
  const tone = status === "ready" ? "ok" : status === "failed" ? "bad" : "wait";
  return (
    <span className={`pill pill-${tone}`} title={STATUS_HELP[status]}>
      <span className="pill-dot" aria-hidden="true" />
      {STATUS_LABEL[status]}
      {isProcessing(status) ? <span className="visually-hidden"> (in progress, not searchable yet)</span> : null}
    </span>
  );
}

export function VersionTag({ number, tone }: { number: number | null | undefined; tone?: "active" | "muted" | "warn" }) {
  if (number === null || number === undefined) return null;
  return <span className={`vtag${tone ? ` vtag-${tone}` : ""}`}>v{number}</span>;
}

// --- forms -------------------------------------------------------------------------------------

interface FieldProps {
  label: string;
  hint?: string;
  error?: string | null;
  children: (props: { id: string; "aria-describedby"?: string; "aria-invalid"?: boolean }) => ReactNode;
}

export function Field({ label, hint, error, children }: FieldProps) {
  const id = useId();
  const describedBy = [hint ? `${id}-hint` : null, error ? `${id}-error` : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children({ id, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined })}
      {hint ? (
        <span className="field-hint" id={`${id}-hint`}>
          {hint}
        </span>
      ) : null}
      {error ? (
        <span className="field-error" id={`${id}-error`}>
          {error}
        </span>
      ) : null}
    </div>
  );
}

export const TextInput = (props: InputHTMLAttributes<HTMLInputElement>) => <input {...props} className={`input${props.className ? ` ${props.className}` : ""}`} />;
export const TextArea = (props: TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea {...props} className={`input textarea${props.className ? ` ${props.className}` : ""}`} />;
export const Select = (props: SelectHTMLAttributes<HTMLSelectElement>) => <select {...props} className={`input select${props.className ? ` ${props.className}` : ""}`} />;

// --- pagination --------------------------------------------------------------------------------

export function Pagination({ page, totalPages, totalItems, onPage, noun = "items" }: { page: number; totalPages: number; totalItems: number; onPage: (page: number) => void; noun?: string }) {
  if (totalPages <= 1) return <p className="pagination-summary">{totalItems} {noun}</p>;
  return (
    <nav className="pagination" aria-label="Pagination">
      <span className="pagination-summary">
        {totalItems} {noun} · page {page} of {totalPages}
      </span>
      <div className="pagination-buttons">
        <Button size="sm" onClick={() => onPage(page - 1)} disabled={page <= 1}>
          Previous
        </Button>
        <Button size="sm" onClick={() => onPage(page + 1)} disabled={page >= totalPages}>
          Next
        </Button>
      </div>
    </nav>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}
