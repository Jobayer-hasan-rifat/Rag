import type {
  Conversation,
  ConversationDetail,
  Paged,
  SendMessageRequest,
  SendMessageResponse,
} from "../types/api";
import { del, get, patch, post, toPaged } from "./apiClient";

export async function listConversations(page: number, signal?: AbortSignal): Promise<Paged<Conversation>> {
  return toPaged(await get<Conversation[]>("/conversations", { query: { page, page_size: 30 }, signal }));
}

export async function createConversation(title?: string): Promise<Conversation> {
  return (await post<Conversation>("/conversations", title ? { title } : {})).data;
}

/** One page of messages, newest page first (`desc`); the caller shows them oldest first. */
export async function getConversation(
  id: string,
  page: number,
  signal?: AbortSignal,
): Promise<ConversationDetail> {
  return (
    await get<ConversationDetail>(`/conversations/${id}`, {
      query: { page, page_size: 20, order: "desc" },
      signal,
    })
  ).data;
}

export async function renameConversation(id: string, title: string): Promise<Conversation> {
  return (await patch<Conversation>(`/conversations/${id}`, { title })).data;
}

export const deleteConversation = (id: string) => del(`/conversations/${id}`);

/**
 * Ask a question. The same `idempotencyKey` must be used for every retry of the same question: the
 * server then returns the stored answer (or retries the stored question) instead of creating a
 * duplicate.
 */
export async function sendMessage(
  id: string,
  body: SendMessageRequest,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<SendMessageResponse> {
  return (
    await post<SendMessageResponse>(`/conversations/${id}/messages`, body, {
      headers: { "Idempotency-Key": idempotencyKey },
      signal,
    })
  ).data;
}

/** A key that satisfies the server's pattern (1 to 64 of A-Z a-z 0-9 _ . : -). */
export function newIdempotencyKey(): string {
  return `q-${crypto.randomUUID()}`;
}
