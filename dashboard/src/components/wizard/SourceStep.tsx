import type { RssCatalog } from "../../lib/types";

export interface SourceStepProps {
  catalog: RssCatalog;
  selected: string[];
  busy: boolean;
  onChange: (selected: string[]) => void;
  onBack: () => void;
  onSubmit: () => void;
}

export function SourceStep({
  catalog,
  selected,
  busy,
  onChange,
  onBack,
  onSubmit,
}: SourceStepProps) {
  return (
    <form
      className="wizard-form"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <fieldset disabled={busy}>
        <legend>RSS evidence sources</legend>
        <p className="field-help">
          Select at least one source. RSS evidence is required before a digest
          can be generated. Catalog version {catalog.version}.
        </p>
        <div className="source-grid">
          {catalog.sources.map((source) => (
            <label className="check-row source-row" key={source.id}>
              <span className="checkbox-hit-target">
                <input
                  type="checkbox"
                  checked={selected.includes(source.id)}
                  onChange={(event) =>
                    onChange(
                      event.target.checked
                        ? [...selected, source.id]
                        : selected.filter((item) => item !== source.id),
                    )
                  }
                />
              </span>
              <span className="check-label">
                <strong>{source.name}</strong>
                <span className="source-url">{source.url}</span>
              </span>
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
