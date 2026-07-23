export interface TickerChipProps {
  symbol: string;
}

/** Renders a single ticker as a small bordered chip. */
export function TickerChip({ symbol }: TickerChipProps) {
  return (
    <span
      style={{
        border: "1px solid #ffb000",
        padding: "2px 8px",
        borderRadius: "4px",
        fontSize: "12px",
        cursor: "default",
        marginRight: "6px",
      }}
    >
      {symbol}
    </span>
  );
}
