import type { Sentiment } from "../lib/types";

export interface SentimentGaugeProps {
  sentiment: Sentiment;
  "aria-label"?: string;
}

const SENTIMENT_COLORS: Record<Sentiment, string> = {
  bullish: "#00d084",
  bearish: "#ff4d4d",
  neutral: "#7a8595",
};

/** Renders a small horizontal sentiment indicator. */
export function SentimentGauge({
  sentiment,
  "aria-label": ariaLabel,
}: SentimentGaugeProps) {
  return (
    <span
      aria-label={ariaLabel ?? `Sentiment: ${sentiment}`}
      role="img"
      style={{
        display: "inline-block",
        width: "24px",
        height: "4px",
        background: SENTIMENT_COLORS[sentiment],
      }}
    />
  );
}
