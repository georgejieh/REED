export interface DigestItem {
  headline: string;
  summary: string;
  source_name: string;
  source_url: string;
  published_at?: string;
}

export interface Digest {
  id: string;
  source_run_id: string;
  market_window: string;
  title: string;
  summary: string;
  published_at: string;
  items: DigestItem[];
}

export type ProviderName =
  | "openrouter"
  | "ollama"
  | "openai_compatible";

export type MarketWindow =
  | "pre_market"
  | "early_market"
  | "midday"
  | "close"
  | "weekend_recap";

export type RunStatus =
  | "queued"
  | "fetching"
  | "generating"
  | "validating"
  | "published"
  | "failed";

export type SchedulerLease = "inactive" | "leader" | "follower" | "lost";

export interface WizardState {
  provider: ProviderName | null;
  model: string | null;
  endpoint: string | null;
  credential_present: boolean;
  market_windows: MarketWindow[];
  rss_source_ids: string[];
  catalog_version: string;
  complete: boolean;
}

export interface CatalogSource {
  id: string;
  name: string;
  url: string;
}

export interface RssCatalog {
  version: string;
  sources: CatalogSource[];
}

export interface RuntimeStatus {
  scheduler_active: boolean;
  scheduler_leader: boolean;
  scheduler_lease: SchedulerLease;
  latest_run: {
    id: string;
    status: RunStatus;
  } | null;
}

export interface HealthStatus {
  status: string;
  service: string;
}

export interface ManualRunResponse {
  run_id: string;
  status: "published";
  published_digest_id: string;
}
