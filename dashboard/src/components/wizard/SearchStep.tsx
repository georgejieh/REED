export interface SearchStepProps {
  onBack: () => void;
  onContinue: () => void;
}

export function SearchStep({ onBack, onContinue }: SearchStepProps) {
  return (
    <section className="wizard-form" aria-labelledby="search-step-title">
      <fieldset disabled>
        <legend id="search-step-title">Supplemental SearXNG</legend>
        <label className="check-row">
          <span className="checkbox-hit-target">
            <input type="checkbox" />
          </span>
          <span className="check-label">
            Enable bounded supplemental search
          </span>
        </label>
        <label>
          SearXNG endpoint
          <input type="url" value="" readOnly />
        </label>
      </fieldset>
      <div className="availability-note" role="status">
        <strong>Unavailable in this backend</strong>
        <p>
          This optional control is not exposed by the current same-origin API,
          so this screen does not change the backend search setting. A fresh
          setup continues with selected RSS sources only. Search can never
          replace the required RSS minimum.
        </p>
        <dl className="limits-grid">
          <div>
            <dt>Query limit</dt>
            <dd>3 per run</dd>
          </div>
          <div>
            <dt>Result limit</dt>
            <dd>10 per query</dd>
          </div>
          <div>
            <dt>Article limit</dt>
            <dd>5 per run</dd>
          </div>
          <div>
            <dt>Total budget</dt>
            <dd>30 seconds</dd>
          </div>
        </dl>
      </div>
      <div className="wizard-actions">
        <button type="button" onClick={onBack}>
          Back
        </button>
        <button className="primary-button" type="button" onClick={onContinue}>
          Continue
        </button>
      </div>
    </section>
  );
}
