import { useCallback, useEffect, useRef, useState } from "react";
import { listDigests, ApiError } from "../lib/api";
import type { Digest } from "../lib/types";

export interface UseDigestListResult {
  digests: Digest[];
  loading: boolean;
  error: ApiError | null;
  refresh: () => void;
}

/**
 * Loads the digest list. Cancels in-flight requests on unmount or `limit`
 * change via `AbortController`.
 */
export function useDigestList(limit?: number): UseDigestListResult {
  const [digests, setDigests] = useState<Digest[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<ApiError | null>(null);
  const activeRef = useRef<Set<AbortController>>(new Set());

  const load = useCallback(async (targetLimit: number, signal: AbortSignal) => {
    setLoading(true);
    try {
      const next = await listDigests(targetLimit, signal);
      if (!signal.aborted) {
        setDigests(next);
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
  }, []);

  const refresh = useCallback(() => {
    const controller = new AbortController();
    activeRef.current.add(controller);
    void load(limit ?? 20, controller.signal).finally(() => {
      activeRef.current.delete(controller);
    });
  }, [limit, load]);

  useEffect(() => {
    const controller = new AbortController();
    activeRef.current.add(controller);
    void load(limit ?? 20, controller.signal).finally(() => {
      activeRef.current.delete(controller);
    });
    return () => {
      activeRef.current.forEach((ctrl) => ctrl.abort());
      activeRef.current.clear();
    };
  }, [limit, load]);

  return { digests, loading, error, refresh };
}
