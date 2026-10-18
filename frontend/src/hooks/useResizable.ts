import { useCallback, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

function read(key: string, fallback: number, min: number, max: number): number {
  try {
    const value = Number(localStorage.getItem(key));
    return Number.isFinite(value) && value >= min && value <= max ? value : fallback;
  } catch {
    return fallback;
  }
}

/**
 * A width the user can drag or change with the arrow keys; it is remembered between visits.
 * The handle is on the left edge of a right-hand panel, so dragging left makes the panel wider.
 */
export function useResizableWidth(key: string, initial: number, min: number, max: number) {
  const [width, setWidth] = useState(() => read(key, initial, min, max));
  const drag = useRef<{ x: number; width: number } | null>(null);

  const commit = useCallback(
    (next: number) => {
      const clamped = Math.min(max, Math.max(min, Math.round(next)));
      setWidth(clamped);
      try {
        localStorage.setItem(key, String(clamped));
      } catch {
        // a preference only
      }
    },
    [key, max, min],
  );

  const handle = {
    role: "separator" as const,
    "aria-orientation": "vertical" as const,
    "aria-valuemin": min,
    "aria-valuemax": max,
    "aria-valuenow": width,
    tabIndex: 0,
    onPointerDown: (event: PointerEvent<HTMLDivElement>) => {
      drag.current = { x: event.clientX, width };
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    onPointerMove: (event: PointerEvent<HTMLDivElement>) => {
      if (drag.current) setWidth(Math.min(max, Math.max(min, drag.current.width + (drag.current.x - event.clientX))));
    },
    onPointerUp: (event: PointerEvent<HTMLDivElement>) => {
      if (!drag.current) return;
      commit(drag.current.width + (drag.current.x - event.clientX));
      drag.current = null;
    },
    onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
      const step = event.shiftKey ? 64 : 24;
      if (event.key === "ArrowLeft") commit(width + step);
      else if (event.key === "ArrowRight") commit(width - step);
      else if (event.key === "Home") commit(min);
      else if (event.key === "End") commit(max);
      else return;
      event.preventDefault();
    },
  };

  return { width, handle };
}
