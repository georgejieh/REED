import { useState } from "react";
import {
  ApiError,
  completeWizard,
  getWizardState,
  saveCredential,
  saveMarketWindows,
  saveProvider,
  saveRssSources,
} from "../lib/api";
import type {
  MarketWindow,
  ProviderName,
  RssCatalog,
  WizardState,
} from "../lib/types";
import { CompletionStep } from "./wizard/CompletionStep";
import {
  ProviderStep,
  type ProviderDraft,
} from "./wizard/ProviderStep";
import { MarketWindowsStep } from "./wizard/MarketWindowsStep";
import { SearchStep } from "./wizard/SearchStep";
import { SourceStep } from "./wizard/SourceStep";

const STEP_LABELS = [
  "Provider",
  "Windows",
  "Sources",
  "Search",
  "Confirm",
];

export interface WizardProps {
  state: WizardState;
  catalog: RssCatalog;
  allowClose: boolean;
  onStateChange: (state: WizardState) => void;
  onComplete: (state: WizardState) => void;
  onClose: () => void;
}

export function Wizard({
  state,
  catalog,
  allowClose,
  onStateChange,
  onComplete,
  onClose,
}: WizardProps) {
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [providerDraft, setProviderDraft] = useState<ProviderDraft>({
    provider: state.provider ?? "",
    model: state.model ?? "",
    endpoint: state.endpoint ?? "",
    credential: "",
  });
  const [marketWindows, setMarketWindows] = useState<MarketWindow[]>(
    state.market_windows,
  );
  const [sourceIds, setSourceIds] = useState<string[]>(state.rss_source_ids);

  const execute = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await operation();
    } catch (value) {
      setError(
        value instanceof ApiError ? value : new ApiError(0, String(value)),
      );
    } finally {
      setBusy(false);
    }
  };

  const submitProvider = () => {
    void execute(async () => {
      const provider = providerDraft.provider as ProviderName;
      const nextState = await saveProvider(
        provider,
        providerDraft.model,
        providerDraft.endpoint || undefined,
      );
      onStateChange(nextState);
      const credentialNeeded =
        provider === "openrouter" || provider === "openai_compatible";
      if (credentialNeeded && !nextState.credential_present) {
        await saveCredential(providerDraft.credential);
        setProviderDraft((current) => ({ ...current, credential: "" }));
        const confirmedState = await getWizardState();
        onStateChange(confirmedState);
        if (!confirmedState.credential_present) {
          throw new ApiError(
            409,
            "The backend did not confirm that the credential is present.",
          );
        }
      }
      setProviderDraft((current) => ({ ...current, credential: "" }));
      setStep(1);
    });
  };

  const submitWindows = () => {
    void execute(async () => {
      const nextState = await saveMarketWindows(marketWindows);
      onStateChange(nextState);
      setStep(2);
    });
  };

  const submitSources = () => {
    void execute(async () => {
      const nextState = await saveRssSources(sourceIds);
      onStateChange(nextState);
      setStep(3);
    });
  };

  const submitCompletion = () => {
    void execute(async () => {
      const nextState = await completeWizard();
      onStateChange(nextState);
      if (!nextState.complete) {
        throw new ApiError(
          409,
          "The backend did not confirm a complete configuration.",
        );
      }
      onComplete(nextState);
    });
  };

  return (
    <main className="wizard-shell">
      <section className="wizard-panel" aria-labelledby="wizard-title">
        <header className="wizard-header">
          <div>
            <p className="eyebrow">LOCAL-FIRST SETUP</p>
            <h1 id="wizard-title">Configure REED</h1>
            <p>
              Select every operating value explicitly. The browser does not
              choose a provider, model, source, or schedule for you.
            </p>
          </div>
          {allowClose ? (
            <button type="button" onClick={onClose}>
              Close configuration
            </button>
          ) : null}
        </header>

        <ol className="step-list" aria-label="Configuration progress">
          {STEP_LABELS.map((label, index) => (
            <li
              key={label}
              aria-current={index === step ? "step" : undefined}
              className={index < step ? "step-complete" : undefined}
            >
              <span>{String(index + 1).padStart(2, "0")}</span>
              {label}
            </li>
          ))}
        </ol>

        {error ? (
          <div className="inline-alert error-alert" role="alert">
            <strong>Configuration not saved</strong>
            <span>
              {error.status ? `Error ${error.status}: ` : ""}
              {error.message}
            </span>
          </div>
        ) : null}

        {step === 0 ? (
          <ProviderStep
            draft={providerDraft}
            state={state}
            busy={busy}
            onChange={setProviderDraft}
            onSubmit={submitProvider}
          />
        ) : null}
        {step === 1 ? (
          <MarketWindowsStep
            selected={marketWindows}
            busy={busy}
            onChange={setMarketWindows}
            onBack={() => setStep(0)}
            onSubmit={submitWindows}
          />
        ) : null}
        {step === 2 ? (
          <SourceStep
            catalog={catalog}
            selected={sourceIds}
            busy={busy}
            onChange={setSourceIds}
            onBack={() => setStep(1)}
            onSubmit={submitSources}
          />
        ) : null}
        {step === 3 ? (
          <SearchStep
            onBack={() => setStep(2)}
            onContinue={() => setStep(4)}
          />
        ) : null}
        {step === 4 ? (
          <CompletionStep
            state={state}
            busy={busy}
            onBack={() => setStep(3)}
            onComplete={submitCompletion}
          />
        ) : null}
      </section>
    </main>
  );
}
