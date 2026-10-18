import { useCallback, useEffect, useRef, useState } from "react";

import { isAbort } from "../services/errors";

export interface Resource<T> {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  reload: () => void;
  setData: (value: T | undefined | ((current: T | undefined) => T | undefined)) => void;
}

/**
 * Loads data when `deps` change. A newer request cancels the older one, a response that arrives
 * after the page moved on is dropped, and the previous data stays visible while reloading (so a
 * filter change does not blank the list).
 */
export function useResource<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
  enabled = true,
): Resource<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<unknown>(undefined);
  const [loading, setLoading] = useState(enabled);
  const [tick, setTick] = useState(0);
  const loader = useRef(load);
  loader.current = load;

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return undefined;
    }
    const controller = new AbortController();
    setLoading(true);
    loader
      .current(controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setData(value);
        setError(undefined);
        setLoading(false);
      })
      .catch((failure: unknown) => {
        if (controller.signal.aborted || isAbort(failure)) return;
        setError(failure);
        setLoading(false);
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, enabled]);

  const reload = useCallback(() => setTick((value) => value + 1), []);
  return { data, error, loading, reload, setData };
}
