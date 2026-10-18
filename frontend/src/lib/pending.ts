/**
 * The question being sent, remembered for the tab so a reload can reconcile with the server and a
 * retry can reuse the same idempotency key (a different key would create a second question).
 * It holds the user's own question text and the key: no token, no answer.
 */

export interface PendingQuestion {
  key: string;
  content: string;
  documentIds: string[];
  rerank: boolean;
  startedAt: number;
}

const prefix = "rag.pending.";

export function loadPending(conversationId: string): PendingQuestion | null {
  try {
    const raw = sessionStorage.getItem(prefix + conversationId);
    if (!raw) return null;
    const value = JSON.parse(raw) as PendingQuestion;
    return typeof value.key === "string" && typeof value.content === "string" ? value : null;
  } catch {
    return null;
  }
}

export function savePending(conversationId: string, value: PendingQuestion): void {
  try {
    sessionStorage.setItem(prefix + conversationId, JSON.stringify(value));
  } catch {
    // without storage a reload simply forgets the key
  }
}

export function clearPending(conversationId: string): void {
  try {
    sessionStorage.removeItem(prefix + conversationId);
  } catch {
    // nothing to clear
  }
}

/** Forget every remembered question (sign-out, or a session the server ended). */
export function clearPendingQuestions(): void {
  try {
    const keys: string[] = [];
    for (let index = 0; index < sessionStorage.length; index += 1) {
      const key = sessionStorage.key(index);
      if (key?.startsWith(prefix)) keys.push(key);
    }
    keys.forEach((key) => sessionStorage.removeItem(key));
  } catch {
    // nothing to clear
  }
}
