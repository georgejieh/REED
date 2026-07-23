/// <reference types="vite/client" />
import type { Digest, SnapshotResponse } from "./types";

/**
 * Thin typed wrapper around `fetch` for the REED read API.
 *
 * Base URL is read from `VITE_API_BASE` at module load. If unset, falls
 * back to `/` so the production build (served from the same FastAPI
 * process when colocated) and the vite dev proxy (`/api/*` -> backend
 * on :8000) work without configuration.
 *
 * Cross-origin failure mode: if the dashboard is served from a different
 * origin than the API (e.g. dashboard.example.com -> api.example.com),
 * leaving VITE_API_BASE unset causes fetches to hit the dashboard origin
 * and 404 / fail CORS. In that deploy case, set
 * `VITE_API_BASE=https://api.example.com` in the deploy environment.
 */
const BASE: string = import.meta.env.VITE_API_BASE ?? "/";

export class ApiError extends Error {
  public readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "GET" });
  if (!res.ok) {
    throw new ApiError(res.status, `${path}: ${res.statusText}`);
  }
  return (await res.json()) as T;
}

/** GET /api/digests?limit=N. Returns the newest digests first. */
export function listDigests(limit = 20): Promise<Digest[]> {
  return getJson<Digest[]>(`/api/digests?limit=${encodeURIComponent(String(limit))}`);
}

/** GET /api/digests/{id}. Returns one digest by id. */
export function getDigest(id: string): Promise<Digest> {
  return getJson<Digest>(`/api/digests/${encodeURIComponent(id)}`);
}

/** GET /api/snapshot. Returns the live market snapshot from the configured provider. */
export function getSnapshot(): Promise<SnapshotResponse> {
  return getJson<SnapshotResponse>("/api/snapshot");
}
