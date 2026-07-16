const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Portal = "myflorida" | "ridemetro" | "bidnet" | "wisconsin";

export interface CommodityCode {
  code: string;
  title: string;
}

// A run searches either the niche's commodity codes or its keywords, never both.
export type SearchMode = "codes" | "keywords";

export interface Category {
  key: string;
  label: string;
  codes: CommodityCode[];
  keywords: string[];
}

export interface CategoriesResponse {
  categories: Category[];
  search_modes: SearchMode[];
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
  mode?: SearchMode;
  ad_statuses?: string[];
  ad_types?: string[];
  codes?: string[];
  excel_exported?: boolean;
  // RideMetro-only
  label?: string;
  excel_path?: string | null;
  // BidNet, and MyFlorida keyword runs: the keyword being searched right now.
  keyword?: string;
  keywords?: string[];
  // MyFlorida keyword runs only, e.g. "3/11".
  keyword_progress?: string;
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

export type AdType =
  | "agency_decision"
  | "grant_opportunities"
  | "informational_notice"
  | "invitation_to_bid"
  | "invitation_to_negotiate"
  | "request_for_proposals"
  | "public_meeting_notice"
  | "request_for_information"
  | "request_for_statement_of_qualifications"
  | "single_source";

export interface StartMyFloridaScrapeOptions {
  category: string;
  mode: SearchMode;
  // Subsets of the niche's catalog; the UI sends what is still checked.
  codes?: string[];
  keywords?: string[];
  adStatuses?: AdStatus[];
  adTypes?: AdType[];
}

export function startMyFloridaScrape({
  category,
  mode,
  codes = [],
  keywords = [],
  adStatuses = [],
  adTypes = [],
}: StartMyFloridaScrapeOptions): Promise<{
  run_id: string;
  mode: SearchMode;
  codes: string[];
  keywords: string[];
  folder: string;
}> {
  return request("/myflorida/scrape", {
    method: "POST",
    body: JSON.stringify({
      category,
      mode,
      codes,
      keywords,
      ad_statuses: adStatuses,
      ad_types: adTypes,
    }),
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
