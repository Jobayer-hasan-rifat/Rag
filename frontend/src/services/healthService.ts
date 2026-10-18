import type { BackendHealth, ReadinessData } from "../types/health";
import { isAbort } from "./errors";
import { get } from "./apiClient";

export async function fetchBackendHealth(signal?: AbortSignal): Promise<BackendHealth> {
  try {
    const { data } = await get<ReadinessData>("/health/ready", { signal, auth: false });
    return {
      api: "ok",
      database: data.checks.database ?? "unavailable",
      redis: data.checks.redis ?? "unavailable",
    };
  } catch (error) {
    if (isAbort(error)) throw error;
    return { api: "unavailable", database: "unavailable", redis: "unavailable" };
  }
}
