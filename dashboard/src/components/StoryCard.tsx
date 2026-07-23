import type { CSSProperties } from "react";
import { SentimentGauge } from "./SentimentGauge";
import { TickerChip } from "./TickerChip";
import { getSentimentColor, SENTIMENT_LABELS } from "../lib/sentiment";
import type { Story } from "../lib/types";

export interface StoryCardProps {
  story: Story;
}

/** Renders a single story with sentiment bar, gauge, and ticker chips. */
export function StoryCard({ story }: StoryCardProps) {
  const style = { "--sentiment-color": getSentimentColor(story.sentiment) } as CSSProperties;

  return (
    <article className="story-card" style={style}>
      <div className="story-body">
        <h3>{story.headline}</h3>
        <SentimentGauge sentiment={story.sentiment} />
        <p className="story-summary">{story.summary}</p>
        <div className="story-meta">
          <span className="sentiment-label">
            {SENTIMENT_LABELS[story.sentiment]}
          </span>
          <div className="ticker-chips">
            {story.tickers.map((symbol) => (
              <TickerChip key={symbol} symbol={symbol} />
            ))}
          </div>
        </div>
      </div>
    </article>
  );
}
