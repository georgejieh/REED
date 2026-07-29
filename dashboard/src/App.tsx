import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { DigestList } from "./components/DigestList";
import { DigestReader } from "./components/DigestReader";
import { RuntimeStatusBanner } from "./components/RuntimeStatusBanner";
import { Wizard } from "./components/Wizard";
import { useDigestList } from "./hooks/useDigestList";
import { useRuntimeStatus } from "./hooks/useRuntimeStatus";
import { useWizardState } from "./hooks/useWizardState";
import type { MarketWindow } from "./lib/types";
import { ApiError, isAdminSurface, loginOperator } from "./lib/api";

export function App() {
  const adminSurface = isAdminSurface();
  const { digests, loading, error, refresh } = useDigestList(20);
  const wizard = useWizardState(adminSurface);
  const runtime = useRuntimeStatus();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showConfiguration, setShowConfiguration] = useState(false);
  const [operatorSecret, setOperatorSecret] = useState("");
  const [loginError, setLoginError] = useState<ApiError | null>(null);
  const [loggingIn, setLoggingIn] = useState(false);
  const orderedIds = useMemo(
    () => digests.map((digest) => digest.id),
    [digests],
  );

  useEffect(() => {
    setSelectedId((current) => current ?? digests[0]?.id ?? null);
  }, [digests]);

  const handleSelect = useCallback((id: string) => {
    setSelectedId(id);
  }, []);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      const target = event.target;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement
      ) {
        return;
      }
      if (event.key === "r" || event.key === "R") {
        if (!event.ctrlKey && !event.metaKey && !event.altKey) {
          runtime.refresh();
          refresh();
        }
        return;
      }
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      if (orderedIds.length === 0) return;
      event.preventDefault();
      const step = event.key === "ArrowUp" ? -1 : 1;
      const index = orderedIds.findIndex((id) => id === selectedId);
      const nextIndex =
        index === -1
          ? 0
          : Math.max(0, Math.min(orderedIds.length - 1, index + step));
      const nextId = orderedIds[nextIndex] ?? null;
      setSelectedId(nextId);
      if (nextId) {
        window.requestAnimationFrame(() => {
          document.getElementById(`digest-${nextId}`)?.focus();
        });
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [orderedIds, refresh, runtime.refresh, selectedId]);

  if (adminSurface && wizard.loading && !wizard.state) {
    return (
      <main className="full-state" aria-busy="true">
        <p className="state-kicker">CONNECTING TO LOCAL RUNTIME</p>
        <h1>Loading REED</h1>
        <p>Checking configuration and the RSS source catalog.</p>
      </main>
    );
  }

  const handleLogin = async (event: FormEvent) => {
    event.preventDefault();
    setLoggingIn(true);
    setLoginError(null);
    try {
      await loginOperator(operatorSecret);
      setOperatorSecret("");
      wizard.refresh();
    } catch (value) {
      setLoginError(
        value instanceof ApiError ? value : new ApiError(0, String(value)),
      );
    } finally {
      setLoggingIn(false);
    }
  };

  if (adminSurface && (!wizard.state || !wizard.catalog)) {
    if (wizard.error?.status === 403) {
      return (
        <main className="full-state">
          <p className="state-kicker">OPERATOR CONTROL</p>
          <h1>Sign in to administer REED</h1>
          <p>Public digests remain available without operator access.</p>
          <form onSubmit={(event) => void handleLogin(event)}>
            <label>
              Operator secret
              <input
                type="password"
                autoComplete="current-password"
                value={operatorSecret}
                onChange={(event) => setOperatorSecret(event.target.value)}
              />
            </label>
            <button
              className="primary-button"
              type="submit"
              disabled={loggingIn || !operatorSecret}
            >
              {loggingIn ? "Signing in..." : "Sign in"}
            </button>
          </form>
          {loginError ? (
            <p role="alert">
              Error {loginError.status}: {loginError.message}
            </p>
          ) : null}
        </main>
      );
    }
    return (
      <main className="full-state">
        <p className="state-kicker">RUNTIME UNAVAILABLE</p>
        <h1>REED could not load setup state</h1>
        <p>
          {wizard.error
            ? `Error ${wizard.error.status}: ${wizard.error.message}`
            : "The local backend did not return configuration data."}
        </p>
        <button className="primary-button" type="button" onClick={wizard.refresh}>
          Retry connection
        </button>
      </main>
    );
  }

  if (
    adminSurface &&
    wizard.state &&
    wizard.catalog &&
    (!wizard.state.complete || showConfiguration)
  ) {
    return (
      <Wizard
        state={wizard.state}
        catalog={wizard.catalog}
        allowClose={wizard.state.complete}
        onStateChange={wizard.setState}
        onComplete={(state) => {
          wizard.setState(state);
          setShowConfiguration(false);
          runtime.refresh();
          refresh();
        }}
        onClose={() => setShowConfiguration(false)}
      />
    );
  }

  const handleRun = async (marketWindow: MarketWindow) => {
    const published = await runtime.startRun(marketWindow);
    if (published) {
      setSelectedId(null);
      refresh();
    }
  };

  return (
    <div className="app">
      <RuntimeStatusBanner
        health={runtime.health}
        runtime={runtime.runtime}
        loading={runtime.loading}
        running={runtime.running}
        error={runtime.error}
        runError={runtime.runError}
        marketWindows={wizard.state?.market_windows ?? []}
        adminEnabled={adminSurface}
        onRun={(window) => void handleRun(window)}
        onConfigure={() => setShowConfiguration(true)}
        onRefresh={() => {
          runtime.refresh();
          refresh();
        }}
      />
      <div className="terminal-workspace">
        <DigestList
          digests={digests}
          selectedId={selectedId}
          onSelect={handleSelect}
        />
        <DigestReader digestId={selectedId} />
      </div>
      {error ? (
        <div className="archive-warning" role="status">
          <strong>Archive refresh unavailable.</strong> Existing published
          digests remain available. Error {error.status}: {error.message}
        </div>
      ) : null}
      {loading && digests.length === 0 ? (
        <div className="loading-bar" role="status">
          Loading published archive...
        </div>
      ) : null}
    </div>
  );
}
