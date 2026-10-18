import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

import { notifyLibraryChanged } from "../lib/libraryEvents";
import { uploadDocument } from "../services/documentService";
import UploadZone from "./UploadZone";
import { Dialog } from "./ui/Dialog";
import { useToast } from "./ui/Toast";

const UploadContext = createContext<{ open: () => void } | null>(null);

export function useUploadDialog(): { open: () => void } {
  const value = useContext(UploadContext);
  if (!value) throw new Error("useUploadDialog needs an UploadProvider");
  return value;
}

/**
 * One upload dialog for the whole app, opened from the header, the dashboard and the composer.
 * It stays mounted when closed so an upload that is running is not cancelled by closing it.
 */
export function UploadProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const openRef = useRef(false);
  openRef.current = open;
  const toast = useToast();

  const api = useMemo(() => ({ open: () => setOpen(true) }), []);
  const describe = useCallback(
    (file: File) => {
      const message = `“${file.name}” was uploaded and is being processed. It is not searchable until it is ready.`;
      if (!openRef.current) toast.success(message);
      return { message, link: { to: "/documents", label: "See it in Documents" } };
    },
    [toast],
  );
  const upload = useCallback(
    (file: File, handlers: Parameters<typeof uploadDocument>[1]) => uploadDocument(file, handlers),
    [],
  );

  return (
    <UploadContext.Provider value={api}>
      {children}
      <Dialog open={open} keepMounted title="Add documents" onClose={() => setOpen(false)}>
        <UploadZone upload={upload} describe={describe} onFinished={notifyLibraryChanged} label="Choose files to add" />
      </Dialog>
    </UploadContext.Provider>
  );
}
