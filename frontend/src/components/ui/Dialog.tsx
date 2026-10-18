import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { describeError } from "../../services/errors";
import { Button, ErrorNotice } from "./primitives";
import { IconButton } from "./primitives";

interface DialogProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** `drawer` slides in from the right (source inspection); `modal` is centred. */
  variant?: "modal" | "drawer";
  footer?: ReactNode;
  /** Keep the content mounted while closed (for example an upload queue that must keep running). */
  keepMounted?: boolean;
}

/**
 * A modal built on the native <dialog> element: the browser traps focus, makes the page behind
 * inert, closes on Escape and restores focus to the opener.
 */
export function Dialog({ open, title, onClose, children, variant = "modal", footer, keepMounted }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      className={`dialog dialog-${variant}`}
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={(event) => {
        if (event.target === ref.current) onClose(); // a click on the backdrop
      }}
    >
      {open || keepMounted ? (
        <div className="dialog-panel">
          <header className="dialog-header">
            <h2 id={titleId}>{title}</h2>
            <IconButton icon="x" label="Close" onClick={onClose} />
          </header>
          <div className="dialog-body">{children}</div>
          {footer ? <footer className="dialog-footer">{footer}</footer> : null}
        </div>
      ) : null}
    </dialog>
  );
}

interface ConfirmProps {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => Promise<void>;
  onClose: () => void;
}

/** Confirms a consequential action; stays open and explains if the server refuses it. */
export function ConfirmDialog({ open, title, children, confirmLabel, danger, onConfirm, onClose }: ConfirmProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (open) {
      setBusy(false);
      setError(null);
    }
  }, [open]);
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
    } catch (failure) {
      setError(failure);
      setBusy(false);
      return;
    }
    setBusy(false);
  };
  return (
    <Dialog
      open={open}
      title={title}
      onClose={busy ? () => undefined : onClose}
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant={danger ? "danger" : "primary"} onClick={run} busy={busy}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="confirm-text">{children}</div>
      {error ? <ErrorNotice error={error} compact /> : null}
      <span className="visually-hidden" aria-live="polite">
        {error ? describeError(error).message : ""}
      </span>
    </Dialog>
  );
}
