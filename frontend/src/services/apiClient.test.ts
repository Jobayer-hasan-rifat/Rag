import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { get, post } from "./apiClient";
import { ApiError } from "./errors";
import { session } from "./session";

function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => [...data.keys()][index] ?? null,
    removeItem: (key) => void data.delete(key),
    setItem: (key, value) => void data.set(key, value),
  };
}

const ok = (data: unknown, status = 200) =>
  new Response(JSON.stringify({ data, meta: { request_id: "r1", timestamp: "t" } }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const failure = (status: number, code: string, details: Record<string, unknown> = {}, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify({ error: { code, message: code.toLowerCase(), details }, meta: { request_id: "r2", timestamp: "t" } }), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });

describe("api client", () => {
  const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>();

  beforeEach(() => {
    vi.stubGlobal("sessionStorage", memoryStorage());
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    session.set({ access_token: "access-1", refresh_token: "refresh-1", token_type: "bearer", expires_in: 900 });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    session.clear();
  });

  it("sends the bearer token and unwraps the envelope", async () => {
    fetchMock.mockResolvedValueOnce(ok({ hello: "world" }));

    const reply = await get<{ hello: string }>("/things", { query: { page: 2, empty: "", none: undefined } });

    expect(reply.data).toEqual({ hello: "world" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/things?page=2");
    expect(url).not.toContain("empty");
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer access-1");
  });

  it("refreshes once for several simultaneous 401s and repeats each call", async () => {
    let refreshes = 0;
    fetchMock.mockImplementation(async (url, init) => {
      if (url.endsWith("/auth/refresh")) {
        refreshes += 1;
        return ok({ access_token: "access-2", refresh_token: "refresh-2", token_type: "bearer", expires_in: 900 });
      }
      const auth = (init?.headers as Record<string, string>).Authorization;
      return auth === "Bearer access-2" ? ok({ fine: true }) : failure(401, "AUTHENTICATION_ERROR");
    });

    const replies = await Promise.all([get("/a"), get("/b"), get("/c")]);

    expect(replies.map((r) => r.status)).toEqual([200, 200, 200]);
    expect(refreshes).toBe(1); // the backend rotates refresh tokens: a second refresh would revoke the first
    expect(session.refresh()).toBe("refresh-2");
  });

  it("ends the session when the refresh is refused", async () => {
    const cleared = vi.fn();
    const stop = session.onCleared(cleared);
    fetchMock.mockImplementation(async (url) =>
      url.endsWith("/auth/refresh") ? failure(401, "AUTHENTICATION_ERROR") : failure(401, "AUTHENTICATION_ERROR"),
    );

    await expect(get("/secret")).rejects.toMatchObject({ code: "SESSION_EXPIRED", status: 401 });

    expect(session.hasSession()).toBe(false);
    expect(cleared).toHaveBeenCalled();
    stop();
  });

  it("does not try to refresh a failed sign-in", async () => {
    session.clear();
    fetchMock.mockResolvedValueOnce(failure(401, "AUTHENTICATION_ERROR"));

    await expect(post("/auth/login", { email: "a", password: "b" }, { auth: false })).rejects.toMatchObject({
      code: "AUTHENTICATION_ERROR",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the error code, details, request id and Retry-After", async () => {
    fetchMock.mockResolvedValueOnce(failure(429, "RATE_LIMIT_EXCEEDED", { x: 1 }, { "Retry-After": "7" }));

    const error = await get("/limited").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 429, code: "RATE_LIMIT_EXCEEDED", retryAfter: 7, requestId: "r2", details: { x: 1 } });
  });

  it("turns a non-JSON error page into an ApiError and a dropped connection into NETWORK_ERROR", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<html>bad gateway</html>", { status: 502, statusText: "Bad Gateway" }));
    await expect(get("/x")).rejects.toMatchObject({ status: 502, code: "HTTP_ERROR" });

    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    await expect(get("/x")).rejects.toMatchObject({ status: 0, code: "NETWORK_ERROR" });
  });

  it("returns an empty reply for 204", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    expect((await get("/gone")).status).toBe(204);
  });
});
