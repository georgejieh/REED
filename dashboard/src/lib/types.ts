/** Sentiment label attached to a story. */
export type Sentiment = "bullish" | "bearish" | "neutral";

/** Named trading session a digest belongs to. */
export type SessionName = "pre_market" | "early_market" | "midday" | "close" | "weekend_recap";

/** A ticker referenced in a story. */
export interface TickerMention {
  symbol: string;
  exchange?: string;
}

/** A numbered source citation. */
export interface Source {
  id: number;
  name: string;
  url: string;
}

/** A single news story inside a digest. */
export interface Story {
  tickers: string[];
  headline: string;
  summary: string;
  sentiment: Sentiment;
  source_name: string;
  source_url: string;
}

/** A single market data point with provenance. */
export interface MarketSnapshotValue {
  value: string;
  change_pct?: string;
  as_of?: string;
  delayed: boolean;
}

/** Provenance block for the market snapshot. */
export interface MarketSnapshotMeta {
  source: string;
  fetched_at: string;
  values_raw: Record<string, MarketSnapshotValue>;
  delayed: boolean;
}

/** Runtime metadata about how the digest was produced. */
export interface Generation {
  provider: string;
  model: string;
  agent_turns: number;
  tool_calls: number;
  scraped_urls: number;
  fallback_used: boolean;
  duration_ms: number;
}

/** A complete market digest for one session. */
export interface Digest {
  id?: string;
  session: SessionName;
  as_of: string;
  headline: string;
  executive_summary: string;
  market_snapshot: Record<string, string>;
  market_snapshot_meta: MarketSnapshotMeta;
  stories: Story[];
  themes: string[];
  watch_next_session: string[];
  sources: Source[];
  generation: Generation;
}

/** Response shape of `GET /api/snapshot`. The endpoint returns its own
 * meta + values shape, distinct from the stored Digest's
 * market_snapshot + market_snapshot_meta pair, because the snapshot is a
 * fresh provider fetch rather than a persisted digest run. */
export interface SnapshotResponse {
  meta: { source: string; fetched_at: string; delayed: boolean };
  values: Record<string, {
    value: string;
    change_pct: string | null;
    as_of: string | null;
    delayed: boolean;
  }>;
}
