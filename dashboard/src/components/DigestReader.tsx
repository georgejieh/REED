import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getDigest } from "../lib/api";
import { formatDateTime, formatLabel } from "../lib/format";
import type { Digest } from "../lib/types";

export interface DigestReaderProps {
  digestId: string | null;
}

export function DigestReader({ digestId }: DigestReaderProps) {
  const [digest, setDigest] = useState<Digest | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);
  const requestSequence = useRef(0);

  const load = useCallback(async (id: string, signal?: AbortSignal) => {
    const requestId = ++requestSequence.current;
    setLoading(true);
    try {
      const nextDigest = await getDigest(id, signal);
      if (requestSequence.current !== requestId) return;
      setDigest(nextDigest);
      setError(null);
    } catch (value) {
      if (signal?.aborted || requestSequence.current !== requestId) return;
      setError(
        value instanceof ApiError ? value : new ApiError(0, String(value)),
      );
    } finally {
      if (requestSequence.current === requestId) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!digestId) {
      requestSequence.current += 1;
      setDigest(null);
      setError(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    void load(digestId, controller.signal);
    return () => {
      controller.abort();
      requestSequence.current += 1;
    };
  }, [digestId, load]);

  if (!digest && loading) {
    return (
      <section className="digest-reader reader-state" aria-busy="true">
        <p className="state-kicker">RETRIEVING PUBLICATION</p>
        <h2>Loading digest</h2>
        <p>Reading immutable published content from the local backend.</p>
      </section>
    );
  }
  if (!digest && error) {
    return (
      <section className="digest-reader reader-state">
        <div className="inline-alert error-alert" role="alert">
          <strong>Digest unavailable</strong>
          <span>
            Error {error.status}: {error.message}
          </span>
          {digestId ? (
            <button type="button" onClick={() => void load(digestId)}>
              Retry
            </button>
          ) : null}
        </div>
      </section>
    );
  }
  if (!digest) {
    return (
      <section className="digest-reader reader-state">
        <p className="state-kicker">READ-ONLY READER</p>
        <h2>No digest selected</h2>
        <p>Select a published digest from the archive.</p>
      </section>
    );
  }

  return (
    <section
      className="digest-reader"
      aria-label="Digest reader"
      aria-busy={loading}
    >
      {error ? (
        <div className="inline-alert error-alert reader-alert" role="alert">
          <strong>Selected digest unavailable</strong>
          <span>
            The last readable digest remains displayed. Error {error.status}:{" "}
            {error.message}
          </span>
          {digestId ? (
            <button type="button" onClick={() => void load(digestId)}>
              Retry
            </button>
          ) : null}
        </div>
      ) : null}
      {loading ? (
        <div className="reader-progress" role="status">
          Loading selected digest...
        </div>
      ) : null}
      <header className="reader-header">
        <p className="eyebrow">IMMUTABLE PUBLICATION</p>
        <h2>{digest.title}</h2>
        <dl className="digest-metadata">
          <div>
            <dt>Market window</dt>
            <dd>{formatLabel(digest.market_window)}</dd>
          </div>
          <div>
            <dt>Published</dt>
            <dd>
              <time dateTime={digest.published_at}>
                {formatDateTime(digest.published_at)}
              </time>
            </dd>
          </div>
          <div>
            <dt>Source run</dt>
            <dd className="identifier">{digest.source_run_id}</dd>
          </div>
        </dl>
      </header>
      <section className="executive-summary" aria-labelledby="summary-heading">
        <h3 id="summary-heading">Executive summary</h3>
        <p>{digest.summary}</p>
      </section>
      <div className="story-list">
        {digest.items.map((item, index) => (
          <article className="story-card" key={`${item.headline}-${index}`}>
            <span className="story-number">
              {String(index + 1).padStart(2, "0")}
            </span>
            <div className="story-body">
              <h3>{item.headline}</h3>
              <div className="market-signals" aria-label="Market analysis">
                <span
                  className={`sentiment-badge sentiment-${["bullish", "bearish", "mixed", "neutral"].includes(item.market_sentiment) ? item.market_sentiment : "neutral"}`}
                >
                  {["bullish", "bearish", "mixed", "neutral"].includes(item.market_sentiment)
                    ? item.market_sentiment
                    : "neutral"} implication
                </span>
                {item.tickers.map((ticker) => (
                  <span className="ticker-chip" key={ticker}>
                    {ticker}
                  </span>
                ))}
              </div>
              <p className="story-summary">{item.summary}</p>
              <p className="market-relevance">
                <strong>Why it matters:</strong>{" "}
                {item.market_relevance || "Relevance not recorded for this historical digest."}
              </p>
              <dl className="source-evidence">
                <div>
                  <dt>Source</dt>
                  <dd>{item.source_name}</dd>
                </div>
                {item.published_at ? (
                  <div>
                    <dt>Source time</dt>
                    <dd>
                      <time dateTime={item.published_at}>
                        {formatDateTime(item.published_at)}
                      </time>
                    </dd>
                  </div>
                ) : (
                  <div>
                    <dt>Evidence time</dt>
                    <dd>Not exposed by this API</dd>
                  </div>
                )}
                <div className="source-link-row">
                  <dt>Evidence URL</dt>
                  <dd>
                    <a
                      href={item.source_url}
                      target="_blank"
                      rel="noreferrer noopener"
                    >
                      {item.source_url}
                    </a>
                  </dd>
                </div>
              </dl>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
