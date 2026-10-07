import type { BackendHealth, ReadinessData, ResponseEnvelope } from "../types/health";
import { getJson } from "./apiClient";

export async function fetchBackendHealth(signal?: AbortSignal): Promise<BackendHealth> {
  try {
    const { body } = await getJson<ResponseEnvelope<ReadinessData>>(
      "/api/v1/health/ready",
      signal,
    );
    const checks = body.data.checks;
    return {
      api: "ok",
      database: checks.database ?? "unavailable",
      redis: checks.redis ?? "unavailable",
    };
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    return { api: "unavailable", database: "unavailable", redis: "unavailable" };
  }
}
