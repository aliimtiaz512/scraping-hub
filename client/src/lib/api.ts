const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Portal = "myflorida" | "ridemetro" | "bidnet" | "wisconsin";

export interface CommodityCode {
  code: string;
  priority: "high" | "medium" | "related";
  title: string;
}

export interface Category {
  key: string;
  label: string;
  codes: CommodityCode[];
}

export interface CategoriesResponse {
  categories: Category[];
  priority_levels: string[];
}

export type KeywordTier = "tier1" | "tier2";

export interface KeywordItem {
  term: string;
  tier: KeywordTier;
  notes: string;
}

export interface KeywordGroup {
  key: string;
  label: string;
  keywords: KeywordItem[];
}

export interface KeywordCatalog {
  groups: KeywordGroup[];
}

export interface BidResult {
  // MyFlorida
  number?: string;
  title?: string;
  // RideMetro
  ref_number?: string;
  project?: string;
  // BidNet
  reference_number?: string;
  solicitation_type?: string;
  closing_date?: string;
  documents_count?: string;
  matched_keyword?: string;
  // Wisconsin
  event_number?: string;
  solicitation_reference?: string;
  event_type?: string;
  event_title?: string;
  agency?: string;
  event_status?: string;
  due_datetime?: string;
  // shared
  documents: string[];
  error: string | null;
  document_errors?: string[];
}

export interface RunStatus {
  run_id: string;
  scraper?: Portal;
  status: "pending" | "running" | "completed" | "failed";
  step: string;
  // MyFlorida-only
  category?: string;
  category_label?: string;
  priority?: string;
  ad_statuses?: string[];
  codes?: string[];
  excel_exported?: boolean;
  // RideMetro-only
  label?: string;
  excel_path?: string | null;
  // BidNet-only
  keyword?: string;
  keywords?: string[];
  // Wisconsin-only
  search?: string;
  agency?: string;
  nigp_code?: string;
  // shared
  started_at: string;
  finished_at: string | null;
  folder: string;
  bids_found: number;
  bids_processed: number;
  documents_downloaded: number;
  errors: string[];
  bids: BidResult[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? `Request failed: ${response.status}`);
  }
  return response.json();
}

// -- MyFlorida ---------------------------------------------------------------

export function getCategories(): Promise<CategoriesResponse> {
  return request("/myflorida/categories");
}

export type AdStatus = "preview" | "open" | "closed" | "withdrawn";

export function startMyFloridaScrape(
  category: string,
  priority: string,
  adStatuses: AdStatus[] = [],
): Promise<{ run_id: string; codes: string[]; folder: string }> {
  return request("/myflorida/scrape", {
    method: "POST",
    body: JSON.stringify({ category, priority, ad_statuses: adStatuses }),
  });
}

// -- RideMetro ---------------------------------------------------------------

export function startRideMetroScrape(): Promise<{ run_id: string; folder: string }> {
  return request("/ridemetro/scrape", { method: "POST" });
}

// -- BidNet Direct -----------------------------------------------------------

export function getBidnetKeywords(): Promise<KeywordCatalog> {
  return request("/bidnet/keywords");
}

export function startBidnetScrape(
  keywords: string[],
): Promise<{ run_id: string; keywords: string[]; folder: string }> {
  return request("/bidnet/scrape", {
    method: "POST",
    body: JSON.stringify({ keywords }),
  });
}

// -- Wisconsin eSupplier -----------------------------------------------------

export function startWisconsinScrape(
  keyword: string,
  agency: string,
  nigpCode: string,
): Promise<{ run_id: string; search: string; folder: string }> {
  return request("/wisconsin/scrape", {
    method: "POST",
    body: JSON.stringify({ keyword, agency, nigp_code: nigpCode }),
  });
}

// -- shared ------------------------------------------------------------------

export function getRunStatus(portal: Portal, runId: string): Promise<RunStatus> {
  return request(`/${portal}/scrape/status/${runId}`);
}

export function listRuns(portal: Portal): Promise<{ runs: RunStatus[] }> {
  return request(`/${portal}/scrape/runs`);
}
