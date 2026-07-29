import { formatLabel } from "../../lib/format";
import type { MarketWindow } from "../../lib/types";

const MARKET_WINDOWS: MarketWindow[] = [
  "pre_market",
  "early_market",
  "midday",
  "close",
  "weekend_recap",
];

export interface MarketWindowsStepProps {
  selected: MarketWindow[];
  busy: boolean;
  onChange: (selected: MarketWindow[]) => void;
  onBack: () => void;
  onSubmit: () => void;
}

export function MarketWindowsStep({
  selected,
  busy,
  onChange,
  onBack,
  onSubmit,
}: MarketWindowsStepProps) {
  return (
    <form
      className="wizard-form"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <fieldset disabled={busy}>
        <legend>Market windows</legend>
        <p className="field-help">
          Choose at least one schedule. Window boundaries are evaluated in
          America/New_York by the backend.
        </p>
        <div className="check-grid">
          {MARKET_WINDOWS.map((window) => (
            <label className="check-row" key={window}>
              <span className="checkbox-hit-target">
                <input
                  type="checkbox"
                  checked={selected.includes(window)}
                  onChange={(event) =>
                    onChange(
                      event.target.checked
                        ? [...selected, window]
                        : selected.filter((item) => item !== window),
                    )
                  }
                />
              </span>
              <span className="check-label">{formatLabel(window)}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="wizard-actions">
        <button type="button" onClick={onBack} disabled={busy}>
          Back
        </button>
        <button
          className="primary-button"
          type="submit"
          disabled={busy || selected.length === 0}
        >
          {busy ? "Saving..." : "Save and continue"}
        </button>
      </div>
    </form>
  );
}
