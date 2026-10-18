import type { TokenPair, User } from "../types/api";
import { get, post } from "./apiClient";
import { session } from "./session";

export async function login(email: string, password: string): Promise<User> {
  const { data } = await post<TokenPair>("/auth/login", { email, password }, { auth: false });
  session.set(data);
  return currentUser();
}

export async function register(email: string, password: string, displayName: string): Promise<User> {
  await post<User>("/auth/register", { email, password, display_name: displayName }, { auth: false });
  return login(email, password);
}

export async function currentUser(signal?: AbortSignal): Promise<User> {
  return (await get<User>("/auth/me", { signal })).data;
}

/** Revoke the refresh token on the server (best effort) and forget the session locally. */
export async function logout(): Promise<void> {
  const token = session.refresh();
  try {
    if (token) await post("/auth/logout", { refresh_token: token });
  } catch {
    // the session is dropped locally either way
  } finally {
    session.clear();
  }
}
