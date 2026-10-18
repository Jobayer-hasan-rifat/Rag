/**
 * The one place that talks to the backend: base URL, bearer token, token refresh, error parsing.
 *
 * A 401 on an authenticated call triggers exactly one refresh (shared by every call that fails at
 * the same time, because the backend rotates refresh tokens and a second concurrent refresh would
 * revoke the first), then the call is repeated once. If the refresh fails the session is cleared.
 */

import type { Envelope, Meta, PageMeta, Paged, TokenPair } from "../types/api";
import { ApiError } from "./errors";
import { session } from "./session";

const BASE = String(import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/+$/, "");
export const API_ROOT = `${BASE}/api/v1`;

export type Query = Record<string, string | number | boolean | null | undefined>;

export interface RequestOptions {
  json?: unknown;
  query?: Query;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Send the bearer token and recover from an expired one (default true). */
  auth?: boolean;
}

export interface Reply<T> {
  status: number;
  data: T;
  meta: Meta | PageMeta;
  headers: Headers;
}

function buildUrl(path: string, query?: Query): string {
  const url = new URL(`${API_ROOT}${path}`);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
  }
  return url.toString();
}

async function toError(response: Response): Promise<ApiError> {
  let code = "HTTP_ERROR";
  let message = response.statusText || "Request failed";
  let details: Record<string, unknown> = {};
  let requestId: string | null = null;
  try {
    const body = (await response.json()) as {
      error?: { code?: string; message?: string; details?: Record<string, unknown> };
      meta?: { request_id?: string | null };
    };
    code = body.error?.code ?? code;
    message = body.error?.message ?? message;
    details = body.error?.details ?? {};
    requestId = body.meta?.request_id ?? null;
  } catch {
    // not JSON (a proxy error page, for example): keep the status text
  }
  const header = Number(response.headers.get("Retry-After"));
  return new ApiError(response.status, code, message, details, requestId, Number.isFinite(header) && header > 0 ? header : null);
}

let refreshing: Promise<boolean> | null = null;

/** Exchange the refresh token for a new pair; resolves false (and clears the session) on failure. */
export function refreshSession(): Promise<boolean> {
  if (refreshing) return refreshing;
  const token = session.refresh();
  if (!token) return Promise.resolve(false);
  refreshing = (async () => {
    try {
      const response = await fetch(buildUrl("/auth/refresh"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: token }),
      });
      if (!response.ok) {
        if (response.status === 401 || response.status === 403 || response.status === 422) session.clear();
        return false;
      }
      const body = (await response.json()) as Envelope<TokenPair>;
      session.set(body.data);
      return true;
    } catch {
      return false; // offline: keep the session so a later call can try again
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

async function send(method: string, path: string, options: RequestOptions, retried: boolean): Promise<Response> {
  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  if (options.json !== undefined) headers["Content-Type"] = "application/json";
  const wantsAuth = options.auth !== false;
  const token = session.access();
  if (wantsAuth && token) headers.Authorization = `Bearer ${token}`;
  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      body: options.json !== undefined ? JSON.stringify(options.json) : undefined,
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "NETWORK_ERROR", "Network request failed");
  }
  if (response.status === 401 && wantsAuth && !retried) {
    if (await refreshSession()) return send(method, path, options, true);
    session.clear();
    throw new ApiError(401, "SESSION_EXPIRED", "Your session has expired");
  }
  return response;
}

export async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<Reply<T>> {
  const response = await send(method, path, options, false);
  if (!response.ok) throw await toError(response);
  if (response.status === 204) {
    return { status: 204, data: undefined as T, meta: { request_id: null, timestamp: "" }, headers: response.headers };
  }
  const body = (await response.json()) as { data: T; meta: Meta | PageMeta };
  return { status: response.status, data: body.data, meta: body.meta, headers: response.headers };
}

export const get = <T>(path: string, options?: RequestOptions) => request<T>("GET", path, options);
export const post = <T>(path: string, json?: unknown, options?: RequestOptions) =>
  request<T>("POST", path, { ...options, json });
export const patch = <T>(path: string, json: unknown, options?: RequestOptions) =>
  request<T>("PATCH", path, { ...options, json });
export const del = (path: string, options?: RequestOptions) => request<void>("DELETE", path, options);

export function toPaged<T>(reply: Reply<T[]>): Paged<T> {
  return { items: reply.data, page: reply.meta as PageMeta };
}

/** Multipart upload with progress (fetch cannot report upload progress, XMLHttpRequest can). */
export function upload<T>(
  path: string,
  file: File,
  handlers: { onProgress?: (fraction: number) => void; signal?: AbortSignal } = {},
): Promise<Reply<T>> {
  const attempt = (retried: boolean): Promise<Reply<T>> =>
    new Promise<Reply<T>>((resolve, reject) => {
      const form = new FormData();
      form.append("file", file, file.name);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", buildUrl(path));
      const token = session.access();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) handlers.onProgress?.(event.loaded / event.total);
      };
      xhr.onerror = () => reject(new ApiError(0, "NETWORK_ERROR", "Network request failed"));
      xhr.onabort = () => reject(new DOMException("Aborted", "AbortError"));
      handlers.signal?.addEventListener("abort", () => xhr.abort(), { once: true });
      xhr.onload = () => {
        const headers = new Headers();
        const retryAfter = xhr.getResponseHeader("Retry-After");
        if (retryAfter) headers.set("Retry-After", retryAfter);
        if (xhr.status === 401 && !retried) {
          refreshSession()
            .then((ok) => {
              if (!ok) {
                session.clear();
                reject(new ApiError(401, "SESSION_EXPIRED", "Your session has expired"));
                return undefined;
              }
              return attempt(true).then(resolve, reject);
            })
            .catch(reject);
          return;
        }
        let body: { data?: T; meta?: Meta; error?: { code?: string; message?: string; details?: Record<string, unknown> } } = {};
        try {
          body = JSON.parse(xhr.responseText) as typeof body;
        } catch {
          // not JSON
        }
        if (xhr.status >= 200 && xhr.status < 300 && body.data !== undefined) {
          resolve({ status: xhr.status, data: body.data, meta: body.meta ?? { request_id: null, timestamp: "" }, headers });
        } else {
          const wait = Number(retryAfter);
          reject(
            new ApiError(
              xhr.status,
              body.error?.code ?? "HTTP_ERROR",
              body.error?.message ?? "Upload failed",
              body.error?.details ?? {},
              body.meta?.request_id ?? null,
              Number.isFinite(wait) && wait > 0 ? wait : null,
            ),
          );
        }
      };
      xhr.send(form);
    });
  return attempt(false);
}

/** Fetch a file with the bearer token and hand it to the browser as a download. */
export async function download(path: string, filename: string): Promise<void> {
  const response = await send("GET", path, {}, false);
  if (!response.ok) throw await toError(response);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
