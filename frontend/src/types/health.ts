export type ServiceState = "checking" | "ok" | "unavailable";

export interface ReadinessData {
  status: "ready" | "unavailable";
  checks: Record<string, "ok" | "unavailable">;
}

export interface ResponseEnvelope<T> {
  data: T;
  meta: { request_id: string | null; timestamp: string };
}

export interface BackendHealth {
  api: ServiceState;
  database: ServiceState;
  redis: ServiceState;
}
