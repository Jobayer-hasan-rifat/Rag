/** One error type for every failed call, and the plain-language text shown to the user. */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
    readonly requestId: string | null = null,
    readonly retryAfter: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface UserFacingError {
  title: string;
  message: string;
  /** Repeating the same request may succeed (network trouble, a busy server, a rate limit). */
  retryable: boolean;
}

type Known = { title: string; message: string; retryable?: boolean };

const BY_CODE: Record<string, Known> = {
  NETWORK_ERROR: {
    title: "Can't reach the server",
    message: "Check your connection and that the API is running, then try again.",
    retryable: true,
  },
  SESSION_EXPIRED: { title: "Session expired", message: "Please sign in again to continue." },
  AUTHENTICATION_ERROR: { title: "Sign-in failed", message: "The email or password is not correct." },
  RATE_LIMIT_EXCEEDED: {
    title: "Too many requests",
    message: "You are going a little fast. Wait a moment and try again.",
    retryable: true,
  },
  RAG_DISABLED: {
    title: "Answers are turned off",
    message: "Answer generation is not enabled on this server, so questions cannot be answered.",
  },
  RERANKING_DISABLED: {
    title: "Reranking is turned off",
    message: "Reranking is not enabled on this server. Search without it.",
  },
  EMBEDDING_UNAVAILABLE: {
    title: "Search is unavailable",
    message: "The search model is not available right now. Try again shortly.",
    retryable: true,
  },
  GENERATION_FAILED: {
    title: "No usable answer",
    message: "The language model did not return a usable answer. Your question was kept; you can retry it.",
    retryable: true,
  },
  CONVERSATION_BUSY: {
    title: "Another question is being answered",
    message: "This conversation answers one question at a time. Wait for it to finish.",
    retryable: true,
  },
  MESSAGE_IN_PROGRESS: {
    title: "Still working on this question",
    message: "This question is already being answered. The answer will appear when it is ready.",
    retryable: true,
  },
  IDEMPOTENCY_KEY_REUSED: {
    title: "Question changed",
    message: "That retry no longer matches the original question. Send it as a new question.",
  },
  CONVERSATION_LIMIT: {
    title: "Limit reached",
    message:
      "This conversation or your account has reached its size limit. Start a new conversation or delete old ones.",
  },
  DUPLICATE_DOCUMENT: {
    title: "Already uploaded",
    message: "An identical file is already in your library.",
  },
  VERSION_LIMIT: {
    title: "Too many versions",
    message: "This document has reached its version limit. Delete an old version first.",
  },
  VERSION_STATE_CONFLICT: {
    title: "That is not possible right now",
    message: "The version's state does not allow this action. Refresh and check its status.",
  },
  INVALID_STATE_TRANSITION: {
    title: "That is not possible right now",
    message: "The item is not in a state that allows this action. Refresh and try again.",
  },
  FILE_TOO_LARGE: { title: "File too large", message: "This file is larger than the upload limit." },
  REQUEST_TOO_LARGE: { title: "Request too large", message: "The request is larger than the server accepts." },
  UNSUPPORTED_FILE_TYPE: {
    title: "Unsupported file",
    message: "Only PDF, DOCX, TXT and Markdown files are supported, and the extension must match the content.",
  },
  INVALID_FILE: {
    title: "The file could not be used",
    message: "The file is damaged or does not match its type.",
  },
  QUOTA_EXCEEDED: {
    title: "Storage full",
    message: "You have reached your storage limit. Delete documents or old versions to free space.",
  },
  STORAGE_UNAVAILABLE: {
    title: "Storage unavailable",
    message: "The file store is not available right now. Try again shortly.",
    retryable: true,
  },
  NOT_FOUND: {
    title: "Not found",
    message: "This item does not exist or you do not have access to it.",
  },
};

const GENERATION_REASONS: Record<string, string> = {
  busy: "The answer model is busy with other requests. Try again in a moment.",
  timeout: "The answer model took too long. Your question was kept; you can retry it.",
  unavailable: "The answer model is not reachable. Your question was kept; you can retry it.",
  rate_limited: "The answer model is rate limited. Wait a little and retry.",
};

export function describeError(error: unknown): UserFacingError {
  if (error instanceof ApiError) {
    if (error.code === "GENERATION_UNAVAILABLE") {
      const reason = String(error.details.reason ?? "unavailable");
      return {
        title: "The answer could not be generated",
        message: GENERATION_REASONS[reason] ?? GENERATION_REASONS.unavailable,
        retryable: true,
      };
    }
    if (error.code === "VALIDATION_ERROR") {
      return { title: "Check the form", message: validationText(error), retryable: false };
    }
    const known = BY_CODE[error.code];
    if (known) {
      const wait =
        error.code === "RATE_LIMIT_EXCEEDED" && error.retryAfter
          ? ` Try again in about ${error.retryAfter} seconds.`
          : "";
      return { title: known.title, message: known.message + wait, retryable: known.retryable ?? false };
    }
    if (error.status >= 500) {
      return {
        title: "Something went wrong on the server",
        message: "Try again in a moment. If it keeps happening, check the server logs.",
        retryable: true,
      };
    }
    return { title: "Request failed", message: error.message, retryable: false };
  }
  return {
    title: "Unexpected error",
    message: error instanceof Error ? error.message : "Something went wrong.",
    retryable: false,
  };
}

interface FieldIssue {
  field?: string;
  location?: string[];
  loc?: unknown[];
  message?: string;
  msg?: string;
}

function validationText(error: ApiError): string {
  const fields = error.details.fields ?? error.details.errors;
  if (Array.isArray(fields) && fields.length > 0) {
    const first = fields[0] as FieldIssue;
    const path = first.location ?? first.loc ?? [];
    const where = first.field ?? (Array.isArray(path) ? path.slice(1).join(".") : "");
    const what = first.message ?? first.msg ?? error.message;
    return where ? `${where}: ${what}` : what;
  }
  return error.message || "Some of the values are not valid.";
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
