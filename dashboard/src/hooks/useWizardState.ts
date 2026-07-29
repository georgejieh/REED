import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  getRssCatalog,
  getWizardState,
  initializeControlSession,
} from "../lib/api";
import type { RssCatalog, WizardState } from "../lib/types";

export interface UseWizardStateResult {
  state: WizardState | null;
  catalog: RssCatalog | null;
  loading: boolean;
  error: ApiError | null;
  setState: (state: WizardState) => void;
  refresh: () => void;
}

export function useWizardState(enabled = true): UseWizardStateResult {
  const [state, setState] = useState<WizardState | null>(null);
  const [catalog, setCatalog] = useState<RssCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const activeRef = useRef<AbortController | null>(null);

  const refresh = useCallback(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    activeRef.current?.abort();
    const controller = new AbortController();
    activeRef.current = controller;
    setLoading(true);
    void initializeControlSession()
      .catch((value: unknown) => {
        if (value instanceof ApiError && [403, 404].includes(value.status)) {
          return;
        }
        throw value;
      })
      .then(() =>
        Promise.all([
          getWizardState(controller.signal),
          getRssCatalog(controller.signal),
        ]),
      )
      .then(([nextState, nextCatalog]) => {
        if (controller.signal.aborted) return;
        setState(nextState);
        setCatalog(nextCatalog);
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
  }, [enabled]);

  useEffect(() => {
    refresh();
    return () => activeRef.current?.abort();
  }, [refresh]);

  return { state, catalog, loading, error, setState, refresh };
}
