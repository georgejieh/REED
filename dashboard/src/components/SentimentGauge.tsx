import type { CSSProperties } from "react";
import { getSentimentColor, SENTIMENT_LABELS } from "../lib/sentiment";
import type { Sentiment } from "../lib/types";

export interface SentimentGaugeProps {
  sentiment: Sentiment;
  "aria-label"?: string;
}

/** Renders a small horizontal sentiment indicator. */
export function SentimentGauge({
  sentiment,
  "aria-label": ariaLabel,
}: SentimentGaugeProps) {
  const style = { "--sentiment-color": getSentimentColor(sentiment) } as CSSProperties;

  return (
    <span
      className="sentiment-gauge"
      aria-label={ariaLabel ?? `Sentiment: ${SENTIMENT_LABELS[sentiment]}`}
      role="img"
      style={style}
    />
  );
}
