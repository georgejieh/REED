import type { Sentiment } from "./types";

const SENTIMENT_COLORS: Record<Sentiment, string> = {
  bullish: "var(--bullish)",
  bearish: "var(--bearish)",
  neutral: "var(--neutral)",
};

export const SENTIMENT_LABELS: Record<Sentiment, string> = {
  bullish: "Bullish",
  bearish: "Bearish",
  neutral: "Neutral",
};

/** Returns the dashboard color token for a sentiment. */
export function getSentimentColor(sentiment: Sentiment): string {
  return SENTIMENT_COLORS[sentiment];
}
