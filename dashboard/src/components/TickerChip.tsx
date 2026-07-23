export interface TickerChipProps {
  symbol: string;
}

/** Renders a single ticker as a small bordered chip. */
export function TickerChip({ symbol }: TickerChipProps) {
  return <span className="ticker-chip">{symbol}</span>;
}
