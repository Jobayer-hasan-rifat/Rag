import { describe, expect, it } from "vitest";

import { newIdempotencyKey } from "./conversationService";
import { ApiError, describeError } from "./errors";

const err = (status: number, code: string, details: Record<string, unknown> = {}, retryAfter: number | null = null) =>
  new ApiError(status, code, "server text", details, null, retryAfter);

describe("user-facing errors", () => {
  it("explains documented conversation errors and marks the retryable ones", () => {
    expect(describeError(err(409, "CONVERSATION_BUSY"))).toMatchObject({ retryable: true });
    expect(describeError(err(409, "MESSAGE_IN_PROGRESS"))).toMatchObject({ retryable: true });
    expect(describeError(err(409, "IDEMPOTENCY_KEY_REUSED"))).toMatchObject({ retryable: false });
    expect(describeError(err(409, "CONVERSATION_LIMIT")).message).toMatch(/limit/i);
  });

  it("names the reason a generation failed and never shows provider detail", () => {
    for (const reason of ["busy", "timeout", "unavailable", "rate_limited"]) {
      const info = describeError(err(503, "GENERATION_UNAVAILABLE", { reason, provider: "secret-host" }));
      expect(info.retryable).toBe(true);
      expect(info.message).not.toContain("secret-host");
    }
    expect(describeError(err(503, "RAG_DISABLED"))).toMatchObject({ retryable: false });
  });

  it("includes the wait for a rate limit", () => {
    expect(describeError(err(429, "RATE_LIMIT_EXCEEDED", {}, 12)).message).toContain("12 seconds");
  });

  it("reads the field a validation error points at", () => {
    const info = describeError(err(422, "VALIDATION_ERROR", { fields: [{ location: ["body", "password"], message: "too short" }] }));
    expect(info.message).toBe("password: too short");
  });

  it("describes network failures as retryable and unknown server errors generically", () => {
    expect(describeError(err(0, "NETWORK_ERROR")).retryable).toBe(true);
    expect(describeError(err(500, "SOMETHING_NEW")).retryable).toBe(true);
    expect(describeError(new Error("boom")).message).toBe("boom");
  });
});

describe("idempotency keys", () => {
  it("satisfy the server pattern and are unique", () => {
    const a = newIdempotencyKey();
    const b = newIdempotencyKey();
    expect(a).toMatch(/^[A-Za-z0-9_.:-]{1,64}$/);
    expect(a).not.toBe(b);
  });
});
