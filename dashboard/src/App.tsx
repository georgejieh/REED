import { useCallback, useEffect, useMemo, useState } from "react";
import { IS_DEMO_BUILD } from "./lib/api";
import { DigestList } from "./components/DigestList";
import { DigestReader } from "./components/DigestReader";
import { useDigestList } from "./hooks/useDigestList";
import { useSnapshot } from "./hooks/useSnapshot";

/** Top-level two-pane layout with keyboard navigation. */
export function App() {
  const { digests, loading, error, refresh } = useDigestList(20, {
    fromDataset: IS_DEMO_BUILD,
  });
  const { snapshot } = useSnapshot({ fromDataset: IS_DEMO_BUILD });
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const orderedIds = useMemo(
    () => digests.map((digest) => digest.id).filter((id): id is string => !!id),
    [digests],
  );

  useEffect(() => {
    setSelectedId((current) => current ?? digests[0]?.id ?? null);
  }, [digests]);

  const handleSelect = useCallback((id: string) => {
    setSelectedId(id);
  }, []);

  useEffect(() => {
    /** Handles Arrow keys, Escape, and 'r' refresh. */
    function handleKeyDown(event: KeyboardEvent) {
      const target = event.target;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement
      ) {
        return;
      }

      switch (event.key) {
        case "ArrowUp":
        case "ArrowDown": {
          event.preventDefault();
          const step = event.key === "ArrowUp" ? -1 : 1;
          const index = orderedIds.findIndex((id) => id === selectedId);
          const nextIndex =
            index === -1
              ? 0
              : Math.max(0, Math.min(orderedIds.length - 1, index + step));
          const nextId = orderedIds[nextIndex];
          if (nextId) setSelectedId(nextId);
          break;
        }
        case "Enter":
          break;
        case "Escape":
          setSelectedId(null);
          break;
        case "r":
        case "R":
          if (!event.ctrlKey && !event.metaKey && !event.altKey) {
            refresh();
          }
          break;
        default:
          break;
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [orderedIds, refresh, selectedId]);

  return (
    <div className="app">
      <DigestList
        digests={digests}
        selectedId={selectedId}
        onSelect={handleSelect}
      />
      <DigestReader
        digestId={selectedId}
        snapshot={snapshot}
      />
      {error ? (
        <div className="app-error" role="alert">
          List error {error.status}: {error.message}
        </div>
      ) : null}
      {loading && digests.length === 0 ? (
        <div className="app-loading">Loading digests...</div>
      ) : null}
    </div>
  );
}
