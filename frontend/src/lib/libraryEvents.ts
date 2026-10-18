import { useEffect, useRef } from "react";

const target = new EventTarget();

/** Tell every mounted list that documents changed (for example after an upload from the header). */
export function notifyLibraryChanged(): void {
  target.dispatchEvent(new Event("changed"));
}

export function useOnLibraryChanged(callback: () => void): void {
  const latest = useRef(callback);
  latest.current = callback;
  useEffect(() => {
    const handler = () => latest.current();
    target.addEventListener("changed", handler);
    return () => target.removeEventListener("changed", handler);
  }, []);
}
