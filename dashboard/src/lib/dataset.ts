/** Read-only adapter for the bundled dashboard's demo build. */
import sampleJsonRaw from "./example-digest.json?raw";
import { ApiError } from "./api";
import type { Digest, SnapshotResponse } from "./types";

export class DatasetUnavailableError extends ApiError {
  constructor(message: string) {
    super(503, message);
    this.name = "DatasetUnavailableError";
  }
}

function isPrivateHost(hostname: string): boolean {
  if (hostname === "localhost") return true;
  if (hostname === "127.0.0.1" || hostname === "::1") return true;
  const parts = hostname.split(".").map((p) => Number(p));
  if (parts.length !== 4 || parts.some((n) => Number.isNaN(n))) return false;
  const [a, b] = parts as [number, number, number, number];
  if (a === 10) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  return false;
}

function validatedDatasetBase(raw: string | undefined): string | undefined {
  if (!raw) return undefined;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return undefined;
  }
  if (url.protocol !== "https:") return undefined;
  if (isPrivateHost(url.hostname)) return undefined;
  let normalized = raw.replace(/\/$/, "");
  if (!/\/resolve\/[^/]+\/?$/.test(normalized)) return undefined;
  return normalized;
}

export const DATASET_BASE: string | undefined = validatedDatasetBase(
  (import.meta.env.VITE_DATASET_BASE ?? "") as string | undefined,
);

const BAKED_SAMPLE: Digest = JSON.parse(sampleJsonRaw) as Digest;

const EMPTY_SNAPSHOT: SnapshotResponse = {
  meta: { source: "static-demo", fetched_at: new Date(0).toISOString(), delayed: true },
  values: {},
};

function requireBaked(): Digest {
  if (!BAKED_SAMPLE || !BAKED_SAMPLE.id) {
    throw new DatasetUnavailableError("dataset and sample both unavailable");
  }
  return BAKED_SAMPLE;
}

export async function listDigestsFromDataset(
  limit = 20,
  signal?: AbortSignal,
): Promise<Digest[]> {
  signal?.throwIfAborted();
  if (!DATASET_BASE) {
    return [requireBaked()].slice(0, Math.max(1, limit));
  }
  try {
    const res = await fetch(`${DATASET_BASE}/_index.json`, { signal });
    if (res.ok) {
      const ids = (await res.json()) as string[];
      const digests = await Promise.all(
        ids.slice(0, limit).map((id) => getDigestFromDataset(id, signal)),
      );
      return digests;
    }
  } catch {
    // fall through to baked sample
  }
  return [requireBaked()].slice(0, Math.max(1, limit));
}

export async function getDigestFromDataset(
  id: string,
  signal?: AbortSignal,
): Promise<Digest> {
  signal?.throwIfAborted();
  if (DATASET_BASE) {
    try {
      const res = await fetch(`${DATASET_BASE}/${id}.json`, { signal });
      if (res.ok) {
        return (await res.json()) as Digest;
      }
    } catch {
      // fall through to baked sample
    }
  }
  const baked = requireBaked();
  if (baked.id === id) return baked;
  throw new DatasetUnavailableError(`digest ${id} not available in dataset or baked sample`);
}

export async function getSnapshotFromDataset(
  _signal?: AbortSignal,
): Promise<SnapshotResponse> {
  return EMPTY_SNAPSHOT;
}