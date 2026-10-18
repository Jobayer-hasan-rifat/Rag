import { useEffect, useState } from "react";

/** Tracks a CSS media query. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => (typeof window !== "undefined" ? window.matchMedia(query).matches : false));
  useEffect(() => {
    const list = window.matchMedia(query);
    const onChange = () => setMatches(list.matches);
    onChange();
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

/**
 * Publishes the visible viewport (which shrinks when a phone's on-screen keyboard opens) as
 * --app-h and --app-top, so a full-height screen can end above the keyboard instead of behind it.
 */
export function useVisibleViewport(): void {
  useEffect(() => {
    const viewport = window.visualViewport;
    const root = document.documentElement;
    if (!viewport) return undefined;
    const update = () => {
      root.style.setProperty("--app-h", `${Math.round(viewport.height)}px`);
      root.style.setProperty("--app-top", `${Math.round(viewport.offsetTop)}px`);
    };
    update();
    viewport.addEventListener("resize", update);
    viewport.addEventListener("scroll", update);
    return () => {
      viewport.removeEventListener("resize", update);
      viewport.removeEventListener("scroll", update);
      root.style.removeProperty("--app-h");
      root.style.removeProperty("--app-top");
    };
  }, []);
}
