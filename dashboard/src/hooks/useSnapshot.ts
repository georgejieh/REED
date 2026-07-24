import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getSnapshot, getSnapshotFromDataset } from "../lib/api";
import type { SnapshotResponse } from "../lib/types";

export interface UseSnapshotOptions {
  fromDataset?: boolean;
}

export interface UseSnapshotResult {
  snapshot: SnapshotResponse | null;
  loading: boolean;
  error: ApiError | null;
  refresh: () => void;
}

/**
 * Fetches the live market snapshot on mount, then polls every 30s.
 * In demo mode, returns the static "delayed" snapshot from the dataset adapter.
 */
export function useSnapshot(opts: UseSnapshotOptions = {}): UseSnapshotResult {
  const fromDataset = opts.fromDataset ?? false;
  const [snapshot, setSnapshot] = useState<SnapshotResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<ApiError | null>(null);
  const activeRef = useRef<Set<AbortController>>(new Set());

  const load = useCallback(
    async (signal: AbortSignal) => {
      setLoading(true);
      try {
        const next = fromDataset
          ? await getSnapshotFromDataset(signal)
          : await getSnapshot(signal);
        if (!signal.aborted) {
          setSnapshot(next);
          setError(null);
        }
      } catch (err) {
        if (signal.aborted) return;
        if (err instanceof ApiError) {
          setError(err);
        } else {
          setError(new ApiError(0, String(err)));
        }
      } finally {
        if (!signal.aborted) {
          setLoading(false);
        }
      }
    },
    [fromDataset],
  );

  const refresh = useCallback(() => {
    const controller = new AbortController();
    activeRef.current.add(controller);
    void load(controller.signal).finally(() => {
      activeRef.current.delete(controller);
    });
  }, [load]);

  useEffect(() => {
    const run = () => {
      const controller = new AbortController();
      activeRef.current.add(controller);
      void load(controller.signal).finally(() => {
        activeRef.current.delete(controller);
      });
    };
    run();
    const timer = setInterval(run, 30000);
    return () => {
      clearInterval(timer);
      activeRef.current.forEach((ctrl) => ctrl.abort());
      activeRef.current.clear();
    };
  }, [load]);

  return { snapshot, loading, error, refresh };
}