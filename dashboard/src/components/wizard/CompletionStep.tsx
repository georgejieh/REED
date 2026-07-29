import { formatLabel } from "../../lib/format";
import type { WizardState } from "../../lib/types";

export interface CompletionStepProps {
  state: WizardState;
  busy: boolean;
  onBack: () => void;
  onComplete: () => void;
}

export function CompletionStep({
  state,
  busy,
  onBack,
  onComplete,
}: CompletionStepProps) {
  return (
    <section className="wizard-form" aria-labelledby="completion-step-title">
      <h3 id="completion-step-title">Confirm configuration</h3>
      <dl className="configuration-summary">
        <div>
          <dt>Provider</dt>
          <dd>{state.provider ? formatLabel(state.provider) : "Not saved"}</dd>
        </div>
        <div>
          <dt>Model</dt>
          <dd>{state.model ?? "Not saved"}</dd>
        </div>
        <div>
          <dt>Credential</dt>
          <dd>{state.credential_present ? "Backend confirmed" : "Not present"}</dd>
        </div>
        <div>
          <dt>Market windows</dt>
          <dd>
            {state.market_windows.length
              ? state.market_windows.map(formatLabel).join(", ")
              : "None saved"}
          </dd>
        </div>
        <div>
          <dt>RSS sources</dt>
          <dd>{state.rss_source_ids.length} selected</dd>
        </div>
        <div>
          <dt>Supplemental search</dt>
          <dd>Not exposed by this API</dd>
        </div>
      </dl>
      <p className="field-help">
        Setup finishes only after the backend validates the saved
        configuration and returns a complete state.
      </p>
      <div className="wizard-actions">
        <button type="button" onClick={onBack} disabled={busy}>
          Back
        </button>
        <button
          className="primary-button"
          type="button"
          onClick={onComplete}
          disabled={busy}
        >
          {busy ? "Confirming..." : "Finish configuration"}
        </button>
      </div>
    </section>
  );
}
