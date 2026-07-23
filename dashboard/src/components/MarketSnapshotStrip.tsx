import type { SnapshotResponse } from "../lib/types";

export interface MarketSnapshotStripProps {
  snapshot: SnapshotResponse | null;
}

const LABELS: Record<string, string> = {
  "^SPX": "S&P 500",
  "^NDX": "Nasdaq 100",
  "10USY.B": "10Y yield",
  "^VIX": "VIX",
};

const ORDER = ["^SPX", "^NDX", "10USY.B", "^VIX"];

/** Renders a four-tile market snapshot strip with provenance. */
export function MarketSnapshotStrip({ snapshot }: MarketSnapshotStripProps) {
  if (!snapshot) {
    return null;
  }

  const { meta, values } = snapshot;

  return (
    <div className="market-snapshot-strip">
      <div className="snapshot-tiles">
        {ORDER.map((symbol) => {
          const item = values[symbol];
          if (!item) return null;
          const label = LABELS[symbol] ?? symbol;
          return (
            <dl key={symbol} className="snapshot-tile">
              <dt>{label}</dt>
              <dd style={{ fontVariantNumeric: "tabular-nums" }}>
                {item.value}
                {item.change_pct ? ` (${item.change_pct})` : null}
                {item.delayed ? (
                  <span
                    aria-label="delayed"
                    style={{
                      display: "inline-block",
                      width: "6px",
                      height: "6px",
                      borderRadius: "50%",
                      background: "#ffb000",
                      marginLeft: "6px",
                    }}
                  />
                ) : null}
              </dd>
            </dl>
          );
        })}
      </div>
      <p className="snapshot-provenance">
        from {meta.source} at {meta.fetched_at}
      </p>
    </div>
  );
}
