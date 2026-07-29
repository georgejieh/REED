import { useState } from "react";
import { formatLabel } from "../lib/format";
import type {
  HealthStatus,
  MarketWindow,
  RuntimeStatus,
} from "../lib/types";
import type { ApiError } from "../lib/api";

export interface RuntimeStatusBannerProps {
  health: HealthStatus | null;
  runtime: RuntimeStatus | null;
  loading: boolean;
  running: boolean;
  error: ApiError | null;
  runError: ApiError | null;
  marketWindows: MarketWindow[];
  onRun: (marketWindow: MarketWindow) => void;
  onConfigure: () => void;
  onRefresh: () => void;
  adminEnabled: boolean;
}

function statusTone(
  health: HealthStatus | null,
  runtime: RuntimeStatus | null,
  error: ApiError | null,
): string {
  if (error || !health || !runtime) return "unavailable";
  if (
    health.status !== "ok" ||
    !runtime.scheduler_active ||
    runtime.scheduler_lease === "lost" ||
    runtime.latest_run?.status === "failed"
  ) {
    return "degraded";
  }
  return "operational";
}

function statusAnnouncement(
  health: HealthStatus | null,
  runtime: RuntimeStatus | null,
  loading: boolean,
  error: ApiError | null,
  runError: ApiError | null,
): string {
  if (loading && !health && !runtime) return "Checking runtime health.";
  const service = error ? "unavailable" : health?.status ?? "unavailable";
  const scheduler = runtime
    ? runtime.scheduler_active
      ? formatLabel(runtime.scheduler_lease)
      : "inactive"
    : "unavailable";
  const latestRun = runError
    ? "failed"
    : runtime?.latest_run
      ? formatLabel(runtime.latest_run.status)
      : "no runs";
  return `Runtime health. Service ${service}. Scheduler ${scheduler}. Latest run ${latestRun}.`;
}

export function RuntimeStatusBanner({
  health,
  runtime,
  loading,
  running,
  error,
  runError,
  marketWindows,
  onRun,
  onConfigure,
  onRefresh,
  adminEnabled,
}: RuntimeStatusBannerProps) {
  const [selectedWindow, setSelectedWindow] = useState<MarketWindow | "">("");
  const tone = statusTone(health, runtime, error);
  const runFailed = runtime?.latest_run?.status === "failed" || Boolean(runError);
  const announcement = statusAnnouncement(
    health,
    runtime,
    loading,
    error,
    runError,
  );

  return (
    <section className={`status-strip tone-${tone}`} aria-label="Runtime health">
      <p className="visually-hidden" role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </p>
      <div className="brand-block">
        <span className="brand-mark">REED</span>
        <span className="environment-label">
          {adminEnabled ? "OPERATOR TERMINAL" : "PUBLIC READER"}
        </span>
      </div>

      <dl className="status-cells">
        <div>
          <dt>Service</dt>
          <dd>{loading && !health ? "Checking" : health?.status ?? "Unavailable"}</dd>
        </div>
        <div>
          <dt>Scheduler</dt>
          <dd>
            {runtime
              ? runtime.scheduler_active
                ? formatLabel(runtime.scheduler_lease)
                : "Inactive"
              : "Unavailable"}
          </dd>
        </div>
        <div>
          <dt>Latest run</dt>
          <dd>
            {runtime?.latest_run
              ? formatLabel(runtime.latest_run.status)
              : "No runs"}
          </dd>
        </div>
        <div>
          <dt>Overall</dt>
          <dd>{formatLabel(tone)}</dd>
        </div>
      </dl>

      <div className="terminal-actions">
        {adminEnabled ? (
          <>
            <label className="compact-field">
              <span>Run window</span>
              <select
                value={selectedWindow}
                disabled={running || marketWindows.length === 0}
                onChange={(event) =>
                  setSelectedWindow(event.target.value as MarketWindow | "")
                }
              >
                <option value="">Select window</option>
                {marketWindows.map((window) => (
                  <option key={window} value={window}>
                    {formatLabel(window)}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="primary-button"
              type="button"
              disabled={running || !selectedWindow}
              onClick={() => selectedWindow && onRun(selectedWindow)}
            >
              {running ? "Running..." : "Run now"}
            </button>
            <button type="button" onClick={onConfigure}>
              Configuration
            </button>
          </>
        ) : null}
        <button type="button" onClick={onRefresh}>
          Refresh
        </button>
      </div>

      {runFailed ? (
        <div className="run-failure">
          <strong>Current run failed.</strong>{" "}
          {runError?.message ??
            "The run did not publish a digest. The last successful digest remains visible."}
        </div>
      ) : null}
      {error ? (
        <div className="run-failure">
          Runtime status unavailable. Existing published digests remain
          readable.
        </div>
      ) : null}
    </section>
  );
}
