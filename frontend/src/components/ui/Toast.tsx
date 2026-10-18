import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

import { describeError } from "../../services/errors";
import { Icon } from "./Icon";

type Tone = "ok" | "error" | "info";
interface ToastItem {
  id: number;
  tone: Tone;
  text: string;
}

interface ToastApi {
  success: (text: string) => void;
  info: (text: string) => void;
  error: (error: unknown) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const value = useContext(ToastContext);
  if (!value) throw new Error("useToast needs a ToastProvider");
  return value;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const next = useRef(1);

  const dismiss = useCallback((id: number) => setItems((all) => all.filter((item) => item.id !== id)), []);
  const push = useCallback(
    (tone: Tone, text: string) => {
      const id = next.current++;
      setItems((all) => [...all.slice(-3), { id, tone, text }]);
      setTimeout(() => dismiss(id), tone === "error" ? 8000 : 4500);
    },
    [dismiss],
  );
  const api = useMemo<ToastApi>(
    () => ({
      success: (text) => push("ok", text),
      info: (text) => push("info", text),
      error: (error) => {
        const info = describeError(error);
        push("error", `${info.title}. ${info.message}`);
      },
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toasts" role="region" aria-label="Notifications" aria-live="polite">
        {items.map((item) => (
          <div key={item.id} className={`toast toast-${item.tone}`}>
            <Icon name={item.tone === "error" ? "alert" : "check"} size={16} />
            <span>{item.text}</span>
            <button type="button" className="toast-close" onClick={() => dismiss(item.id)} aria-label="Dismiss notification">
              <Icon name="x" size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
