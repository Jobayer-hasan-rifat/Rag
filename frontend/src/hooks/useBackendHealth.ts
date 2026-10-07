import { useCallback, useEffect, useState } from "react";

import { fetchBackendHealth } from "../services/healthService";
import type { BackendHealth } from "../types/health";

const CHECKING: BackendHealth = { api: "checking", database: "checking", redis: "checking" };

export function useBackendHealth(): BackendHealth & { refresh: () => void } {
  const [health, setHealth] = useState<BackendHealth>(CHECKING);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setHealth(CHECKING);
    fetchBackendHealth(controller.signal)
      .then(setHealth)
      .catch(() => undefined);
    return () => controller.abort();
  }, [attempt]);

  const refresh = useCallback(() => setAttempt((value) => value + 1), []);
  return { ...health, refresh };
}
