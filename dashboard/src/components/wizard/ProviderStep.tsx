import type { ProviderName, WizardState } from "../../lib/types";

export interface ProviderDraft {
  provider: ProviderName | "";
  model: string;
  endpoint: string;
  credential: string;
}

export interface ProviderStepProps {
  draft: ProviderDraft;
  state: WizardState;
  busy: boolean;
  onChange: (draft: ProviderDraft) => void;
  onSubmit: () => void;
}

export function ProviderStep({
  draft,
  state,
  busy,
  onChange,
  onSubmit,
}: ProviderStepProps) {
  const endpointRequired =
    draft.provider === "ollama" ||
    draft.provider === "openai_compatible";
  const credentialRequired =
    draft.provider === "openrouter" ||
    draft.provider === "openai_compatible";

  return (
    <form
      className="wizard-form"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <fieldset disabled={busy}>
        <legend>Provider and model</legend>
        <label>
          Provider
          <select
            required
            value={draft.provider}
            onChange={(event) =>
              onChange({
                ...draft,
                provider: event.target.value as ProviderName | "",
                model: "",
                endpoint: "",
                credential: "",
              })
            }
          >
            <option value="">Select a provider</option>
            <option value="openrouter">OpenRouter</option>
            <option value="ollama">Ollama local runtime</option>
            <option value="openai_compatible">
              OpenAI-compatible endpoint
            </option>
          </select>
        </label>

        <label>
          Model identifier
          <input
            required
            type="text"
            value={draft.model}
            spellCheck={false}
            autoComplete="off"
            placeholder="Enter the exact model identifier"
            onChange={(event) =>
              onChange({ ...draft, model: event.target.value })
            }
          />
        </label>

        {endpointRequired ? (
          <label>
            {draft.provider === "ollama"
              ? "Local endpoint"
              : "Provider endpoint"}
            <input
              required
              type="url"
              value={draft.endpoint}
              spellCheck={false}
              autoComplete="off"
              placeholder={
                draft.provider === "ollama"
                  ? "http://127.0.0.1:11434"
                  : "https://provider.example/v1"
              }
              onChange={(event) =>
                onChange({ ...draft, endpoint: event.target.value })
              }
            />
          </label>
        ) : null}

        {credentialRequired && !state.credential_present ? (
          <label>
            Provider credential
            <input
              required
              type="password"
              value={draft.credential}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) =>
                onChange({ ...draft, credential: event.target.value })
              }
            />
          </label>
        ) : null}
      </fieldset>

      <div className="boundary-note" role="note">
        <strong>Credential boundary</strong>
        <p>
          In local mode, this value is sent directly to the same-origin
          backend and stored by the operating system credential vault. It is
          not saved in this browser or the REED database. Hosted credentials
          are managed only by the deployment secret store.
        </p>
      </div>

      {state.credential_present && credentialRequired ? (
        <p className="confirmed-state" role="status">
          The backend confirms that a credential is present. Its value is not
          exposed.
        </p>
      ) : null}

      <div className="wizard-actions">
        <button className="primary-button" type="submit" disabled={busy}>
          {busy ? "Saving..." : "Save and continue"}
        </button>
      </div>
    </form>
  );
}
