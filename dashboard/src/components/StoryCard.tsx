import { SentimentGauge } from "./SentimentGauge";
import { TickerChip } from "./TickerChip";
import type { Story, Sentiment } from "../lib/types";

export interface StoryCardProps {
  story: Story;
}

const SENTIMENT_COLORS: Record<Sentiment, string> = {
  bullish: "#00d084",
  bearish: "#ff4d4d",
  neutral: "#7a8595",
};

/** Renders a single story with sentiment bar, gauge, and ticker chips. */
export function StoryCard({ story }: StoryCardProps) {
  const color = SENTIMENT_COLORS[story.sentiment];

  return (
    <article
      className="story-card"
      style={{ display: "flex", borderLeft: `4px solid ${color}` }}
    >
      <div className="story-body" style={{ paddingLeft: "12px" }}>
        <h3 style={{ fontSize: "16px", fontWeight: 700, margin: "0 0 6px" }}>
          {story.headline}
        </h3>
        <SentimentGauge sentiment={story.sentiment} />
        <p style={{ fontSize: "14px", margin: "8px 0" }}>{story.summary}</p>
        <div className="story-meta">
          <span
            style={{
              fontSize: "11px",
              fontFamily: "ui-monospace, monospace",
              textTransform: "uppercase",
              color,
            }}
          >
            {story.sentiment}
          </span>
          <div className="ticker-chips" style={{ marginTop: "8px" }}>
            {story.tickers.map((symbol) => (
              <TickerChip key={symbol} symbol={symbol} />
            ))}
          </div>
        </div>
      </div>
    </article>
  );
}
