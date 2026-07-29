import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, listDigests } from "../lib/api";
import type { Digest } from "../lib/types";

export interface UseDigestListResult {
  digests: Digest[];
  loading: boolean;
  error: ApiError | null;
  refresh: () => void;
}

export function useDigestList(limit = 20): UseDigestListResult {
  const [digests, setDigests] = useState<Digest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const activeRef = useRef<Set<AbortController>>(new Set());

  const load = useCallback(async (signal: AbortSignal) => {
    setLoading(true);
    try {
      const next = await listDigests(limit, signal);
      if (!signal.aborted) {
        setDigests(next);
        setError(null);
      }
    } catch (value) {
      if (signal.aborted) return;
      setError(
        value instanceof ApiError ? value : new ApiError(0, String(value)),
      );
    } finally {
      if (!signal.aborted) setLoading(false);
    }
  }, [limit]);

  const refresh = useCallback(() => {
    const controller = new AbortController();
    activeRef.current.add(controller);
    void load(controller.signal).finally(() => {
      activeRef.current.delete(controller);
    });
  }, [load]);

  useEffect(() => {
    refresh();
    return () => {
      activeRef.current.forEach((controller) => controller.abort());
      activeRef.current.clear();
    };
  }, [refresh]);

  return { digests, loading, error, refresh };
}
