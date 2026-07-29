/// <reference types="vite/client" />
import type {
  Digest,
  HealthStatus,
  ManualRunResponse,
  MarketWindow,
  ProviderName,
  RssCatalog,
  RuntimeStatus,
  WizardState,
} from "./types";

export class ApiError extends Error {
  public readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const configuredApiBase = (
  import.meta.env.VITE_REED_API_BASE_URL ?? ""
).replace(/\/+$/, "");
let csrfToken: string | null = null;

export function isAdminSurface(): boolean {
  if (!configuredApiBase) return true;
  return new URL(configuredApiBase).origin === window.location.origin;
}

function apiPath(path: string): string {
  const normalized = `/${path.replace(/^\/+/, "")}`;
  return configuredApiBase ? `${configuredApiBase}${normalized}` : normalized;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as {
      detail?: string | { message?: string };
    };
    if (typeof body.detail === "string") return body.detail;
    if (body.detail?.message) return body.detail.message;
  } catch {
    return response.statusText || "Request failed";
  }
  return response.statusText || "Request failed";
}

async function requestJson<T>(
  path: string,
  init: RequestInit,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(apiPath(path), {
    ...init,
    credentials: isAdminSurface() ? "include" : "omit",
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(csrfToken && !["GET", "HEAD"].includes(init.method ?? "GET")
        ? { "X-CSRF-Token": csrfToken }
        : {}),
      ...init.headers,
    },
    signal,
  });
  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response));
  }
  return (await response.json()) as T;
}

async function requestEmpty(
  path: string,
  init: RequestInit,
): Promise<void> {
  const response = await fetch(apiPath(path), {
    ...init,
    credentials: isAdminSurface() ? "include" : "omit",
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response));
  }
}

function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return requestJson<T>(path, { method: "GET" }, signal);
}

export async function initializeControlSession(): Promise<void> {
  if (!isAdminSurface() || csrfToken) return;
  const bootstrap = await fetch(apiPath("/api/auth/bootstrap"), {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!bootstrap.ok) {
    throw new ApiError(bootstrap.status, await errorMessage(bootstrap));
  }
  const session = await requestJson<{ csrf_token: string }>(
    "/api/auth/session",
    { method: "POST" },
  );
  csrfToken = session.csrf_token;
}

export async function loginOperator(secret: string): Promise<void> {
  const session = await requestJson<{ csrf_token: string }>(
    "/api/auth/login",
    {
      method: "POST",
      body: JSON.stringify({ secret }),
    },
  );
  csrfToken = session.csrf_token;
}

function putJson<T>(path: string, body: unknown): Promise<T> {
  return requestJson<T>(path, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function listDigests(limit = 20, signal?: AbortSignal): Promise<Digest[]> {
  return getJson<Digest[]>(
    `/api/digests?limit=${encodeURIComponent(String(limit))}`,
    signal,
  );
}

export function getDigest(id: string, signal?: AbortSignal): Promise<Digest> {
  return getJson<Digest>(`/api/digests/${encodeURIComponent(id)}`, signal);
}

export function getHealth(signal?: AbortSignal): Promise<HealthStatus> {
  return getJson<HealthStatus>("/api/health", signal);
}

export function getRuntimeStatus(
  signal?: AbortSignal,
): Promise<RuntimeStatus> {
  return getJson<RuntimeStatus>("/api/runtime/status", signal);
}

export function getWizardState(signal?: AbortSignal): Promise<WizardState> {
  return getJson<WizardState>("/api/wizard/state", signal);
}

export function getRssCatalog(signal?: AbortSignal): Promise<RssCatalog> {
  return getJson<RssCatalog>("/api/wizard/rss-catalog", signal);
}

export function saveProvider(
  provider: ProviderName,
  model: string,
  endpoint?: string,
): Promise<WizardState> {
  return putJson<WizardState>("/api/wizard/provider", {
    provider,
    model,
    ...(endpoint ? { endpoint } : {}),
  });
}

export function saveCredential(credential: string): Promise<void> {
  return requestEmpty("/api/wizard/credential", {
    method: "PUT",
    body: JSON.stringify({ credential }),
  });
}

export function saveMarketWindows(
  marketWindows: MarketWindow[],
): Promise<WizardState> {
  return putJson<WizardState>("/api/wizard/market-windows", {
    market_windows: marketWindows,
  });
}

export function saveRssSources(sourceIds: string[]): Promise<WizardState> {
  return putJson<WizardState>("/api/wizard/rss-sources", {
    source_ids: sourceIds,
  });
}

export function completeWizard(): Promise<WizardState> {
  return requestJson<WizardState>("/api/wizard/complete", {
    method: "POST",
  });
}

export function runNow(
  marketWindow: MarketWindow,
): Promise<ManualRunResponse> {
  return requestJson<ManualRunResponse>("/api/admin/runs", {
    method: "POST",
    body: JSON.stringify({ market_window: marketWindow }),
  });
}
