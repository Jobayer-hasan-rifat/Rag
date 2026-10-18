/**
 * Where the session lives.
 *
 * The access token (15 minutes) is kept in memory only. The refresh token is kept in
 * sessionStorage: it survives a reload of the tab but not closing it, and is never put in
 * localStorage. Like any script-readable store it is exposed to cross-site scripting, which is why
 * the interface never renders model output or file content as HTML. An HttpOnly cookie would be
 * stronger and needs a backend change (documented as deferred).
 */

import type { TokenPair } from "../types/api";

const REFRESH_KEY = "rag.session.refresh";

let accessToken: string | null = null;
const listeners = new Set<() => void>();

function readRefresh(): string | null {
  try {
    return sessionStorage.getItem(REFRESH_KEY);
  } catch {
    return null;
  }
}

function writeRefresh(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(REFRESH_KEY, token);
    else sessionStorage.removeItem(REFRESH_KEY);
  } catch {
    // storage blocked: the session then lasts until the page is reloaded
  }
}

export const session = {
  access: (): string | null => accessToken,
  refresh: (): string | null => readRefresh(),
  hasSession: (): boolean => accessToken !== null || readRefresh() !== null,
  set(pair: TokenPair): void {
    accessToken = pair.access_token;
    writeRefresh(pair.refresh_token);
  },
  clear(): void {
    accessToken = null;
    writeRefresh(null);
    listeners.forEach((listener) => listener());
  },
  /** Called when the server has refused the session for good. */
  onCleared(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};
