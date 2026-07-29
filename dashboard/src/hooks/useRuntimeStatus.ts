import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  getHealth,
  getRuntimeStatus,
  runNow,
} from "../lib/api";
import type {
  HealthStatus,
  MarketWindow,
  RuntimeStatus,
} from "../lib/types";

export interface UseRuntimeStatusResult {
  health: HealthStatus | null;
  runtime: RuntimeStatus | null;
  loading: boolean;
  running: boolean;
  error: ApiError | null;
  runError: ApiError | null;
  refresh: () => void;
  startRun: (marketWindow: MarketWindow) => Promise<boolean>;
}

export function useRuntimeStatus(): UseRuntimeStatusResult {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [runError, setRunError] = useState<ApiError | null>(null);
  const activeRef = useRef<AbortController | null>(null);

  const refresh = useCallback(() => {
    activeRef.current?.abort();
    const controller = new AbortController();
    activeRef.current = controller;
    setLoading(true);
    void Promise.all([
      getHealth(controller.signal),
      getRuntimeStatus(controller.signal),
    ])
      .then(([nextHealth, nextRuntime]) => {
        if (controller.signal.aborted) return;
        setHealth(nextHealth);
        setRuntime(nextRuntime);
        setError(null);
      })
      .catch((value: unknown) => {
        if (controller.signal.aborted) return;
        setError(
          value instanceof ApiError ? value : new ApiError(0, String(value)),
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
  }, []);

  const startRun = useCallback(
    async (marketWindow: MarketWindow): Promise<boolean> => {
      setRunning(true);
      setRunError(null);
      try {
        await runNow(marketWindow);
        refresh();
        return true;
      } catch (value) {
        setRunError(
          value instanceof ApiError ? value : new ApiError(0, String(value)),
        );
        refresh();
        return false;
      } finally {
        setRunning(false);
      }
    },
    [refresh],
  );

  useEffect(() => {
    refresh();
    const interval = window.setInterval(refresh, 10_000);
    return () => {
      window.clearInterval(interval);
      activeRef.current?.abort();
    };
  }, [refresh]);

  return {
    health,
    runtime,
    loading,
    running,
    error,
    runError,
    refresh,
    startRun,
  };
}
