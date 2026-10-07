const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface ApiResult<T> {
  status: number;
  body: T;
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<ApiResult<T>> {
  const response = await fetch(`${API_BASE_URL}${path}`, { signal });
  return { status: response.status, body: (await response.json()) as T };
}
