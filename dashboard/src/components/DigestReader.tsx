import { useCallback, useEffect, useRef, useState } from "react";
import { getDigest, ApiError } from "../lib/api";
import { MarketSnapshotStrip } from "./MarketSnapshotStrip";
import { SourceFooter } from "./SourceFooter";
import { StoryCard } from "./StoryCard";
import type { Digest, SnapshotResponse } from "../lib/types";

export interface DigestReaderProps {
  digestId: string | null;
  snapshot?: SnapshotResponse | null;
}

function useDigest(id: string | null): {
  digest: Digest | null;
  loading: boolean;
  error: ApiError | null;
  refresh: () => void;
} {
  const [digest, setDigest] = useState<Digest | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<ApiError | null>(null);
  const activeRef = useRef<Set<AbortController>>(new Set());

  const load = useCallback(async (targetId: string, signal: AbortSignal) => {
    setLoading(true);
    try {
      const next = await getDigest(targetId, signal);
      if (!signal.aborted) {
        setDigest(next);
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
    if (id === null) return;
    const controller = new AbortController();
    activeRef.current.add(controller);
    void load(id, controller.signal).finally(() => {
      activeRef.current.delete(controller);
    });
  }, [id, load]);

  useEffect(() => {
    if (id === null) {
      setDigest(null);
      setLoading(false);
      setError(null);
      return;
    }
    const controller = new AbortController();
    activeRef.current.add(controller);
    void load(id, controller.signal).finally(() => {
      activeRef.current.delete(controller);
    });
    return () => {
      activeRef.current.forEach((ctrl) => ctrl.abort());
      activeRef.current.clear();
    };
  }, [id, load]);

  return { digest, loading, error, refresh };
}

/** Renders the right pane: a selected digest with market strip and stories. */
export function DigestReader({ digestId, snapshot }: DigestReaderProps) {
  const { digest, loading, error, refresh } = useDigest(digestId);

  if (loading) {
    return (
      <section className="digest-reader" aria-label="Digest reader">
        <p className="loading">Loading...</p>
      </section>
    );
  }

  if (error) {
    return (
      <section className="digest-reader" aria-label="Digest reader">
        <div className="error">
          <p>
            Error {error.status}: {error.message}
          </p>
          <button type="button" onClick={refresh}>
            Refresh
          </button>
        </div>
      </section>
    );
  }

  if (!digest) {
    return (
      <section className="digest-reader" aria-label="Digest reader">
        <p className="empty">Select a digest from the list.</p>
      </section>
    );
  }

  return (
    <section className="digest-reader" aria-label="Digest reader">
      <header className="reader-header">
        <h2>{digest.headline}</h2>
        <p className="session-time">
          <time dateTime={digest.as_of}>
            {digest.session} / {digest.as_of}
          </time>
        </p>
      </header>

      <p className="executive-summary">{digest.executive_summary}</p>

      <MarketSnapshotStrip snapshot={snapshot ?? null} />

      <div className="story-list">
        {digest.stories.map((story, index) => (
          <StoryCard key={`${story.headline}-${index}`} story={story} />
        ))}
      </div>

      <SourceFooter sources={digest.sources} />

      <div className="watch-next">
        <h3>Watch next session</h3>
        <ul>
          {digest.watch_next_session.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
      </div>
    </section>
  );
}
