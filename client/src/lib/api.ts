const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Portal =
  | "myflorida"
  | "ridemetro"
  | "bidnet"
  | "wisconsin"
  | "northdakota"
  | "septa"
  | "sam"
  | "unison"
  | "naics";

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

export type KeywordTier = "core" | "extended";

export interface BidnetKeyword {
  term: string;
  notes: string;
}

// A niche (AI/ML, Web Scraping, UI/UX) with its two tiers. Results are foldered
// per niche+tier, so the selection UI mirrors this shape.
export interface BidnetNiche {
  key: string;
  label: string;
  /** Used in the produced folder names, e.g. "AI-ML" -> Bidnetdirect_AI-ML_core. */
  slug: string;
  core: BidnetKeyword[];
  extended: BidnetKeyword[];
}

export interface BidnetCatalog {
  niches: BidnetNiche[];
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
  // North Dakota
  rfp_id?: string;
  pub_begin_date?: string;
  pub_end_date?: string;
  begin_date?: string;
  close_date?: string;
  commodity?: string;
  remaining_time?: string;
  status?: string;
  detail_url?: string;
  // SEPTA
  requisition_number?: string;
  summary?: string;
  open_date?: string;
  // SAM.gov
  notice_id?: string;
  department?: string;
  subtier?: string;
  office?: string;
  description?: string;
  updated_date?: string;
  bid_repeat_count?: number;
  naics_code?: string;
  naics_title?: string;
  date_offers_due?: string;
  published_date?: string;
  decision?: string;   // PURSUE | REJECT (PENDING | ERROR only on eval failure)
  reason?: string;
  // Unison
  buyer_number?: string;
  buyer_description?: string;
  buyer?: string;
  end_date?: string;
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
  // SEPTA-only: the optional filters a run was launched with.
  date_filter?: string | null;
  commodity_code?: string | null;
  // SAM-only filters.
  date_from?: string | null;
  date_to?: string | null;
  naics_codes?: string[];
  award_notice?: boolean;
  // Unison-only.
  filter_by?: string | null;
  // shared
  started_at: string;
  finished_at: string | null;
  folder: string;
  bids_found: number;
  bids_processed: number;
  documents_downloaded: number;
  errors: string[];
  // Non-fatal notices, e.g. a keyword that matched nothing (MyFlorida).
  warnings?: string[];
  // True when every search pass returned zero rows — the search worked, the
  // portal simply has nothing matching (MyFlorida).
  no_results?: boolean;
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
  livePreview?: boolean;
}

/** `?live_preview=true` when the user wants to watch the browser work. */
function livePreviewQuery(livePreview?: boolean): string {
  return livePreview ? "?live_preview=true" : "";
}

export function startMyFloridaScrape({
  category,
  mode,
  codes = [],
  keywords = [],
  adStatuses = [],
  adTypes = [],
  livePreview = false,
}: StartMyFloridaScrapeOptions): Promise<{
  run_id: string;
  mode: SearchMode;
  codes: string[];
  keywords: string[];
  folder: string;
}> {
  return request(`/myflorida/scrape${livePreviewQuery(livePreview)}`, {
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

export function startRideMetroScrape(livePreview = false): Promise<{ run_id: string; folder: string }> {
  return request(`/ridemetro/scrape${livePreviewQuery(livePreview)}`, { method: "POST" });
}

// -- BidNet Direct -----------------------------------------------------------

export function getBidnetKeywords(): Promise<BidnetCatalog> {
  return request("/bidnet/keywords");
}

export function startBidnetScrape(
  keywords: string[],
  livePreview = false,
): Promise<{ run_id: string; keywords: string[]; folder: string }> {
  return request(`/bidnet/scrape${livePreviewQuery(livePreview)}`, {
    method: "POST",
    body: JSON.stringify({ keywords }),
  });
}

// -- Wisconsin eSupplier -----------------------------------------------------

export function startWisconsinScrape(
  keyword: string,
  agency: string,
  nigpCode: string,
  livePreview = false,
): Promise<{ run_id: string; search: string; folder: string }> {
  return request(`/wisconsin/scrape${livePreviewQuery(livePreview)}`, {
    method: "POST",
    body: JSON.stringify({ keyword, agency, nigp_code: nigpCode }),
  });
}

// -- North Dakota (ND Buys) --------------------------------------------------

export function startNorthDakotaScrape(
  keyword: string,
  commodity: string,
  livePreview = false,
): Promise<{ run_id: string; search: string; folder: string }> {
  return request(`/northdakota/scrape${livePreviewQuery(livePreview)}`, {
    method: "POST",
    body: JSON.stringify({ keyword, commodity }),
  });
}

// -- SEPTA (vendor procurement portal) ---------------------------------------

export interface StartSeptaScrapeOptions {
  // All optional and freely combinable; all blank = today's open quotes.
  dateFilter?: string;
  keyword?: string;
  commodityCode?: string;
  livePreview?: boolean;
}

export function startSeptaScrape({
  dateFilter = "",
  keyword = "",
  commodityCode = "",
  livePreview = false,
}: StartSeptaScrapeOptions): Promise<{ run_id: string; search: string; folder: string }> {
  return request(`/septa/scrape${livePreviewQuery(livePreview)}`, {
    method: "POST",
    body: JSON.stringify({
      date_filter: dateFilter || null,
      keyword: keyword || null,
      commodity_code: commodityCode || null,
    }),
  });
}

// -- SAM.gov -----------------------------------------------------------------

export interface StartSamScrapeOptions {
  dateFrom?: string;
  dateTo?: string;
  naicsCodes?: string[];
  awardNotice?: boolean;
  livePreview?: boolean;
}

export function startSamScrape({
  dateFrom = "",
  dateTo = "",
  naicsCodes = [],
  awardNotice = false,
  livePreview = false,
}: StartSamScrapeOptions): Promise<{ run_id: string; search: string; folder: string }> {
  return request(`/sam/scrape${livePreviewQuery(livePreview)}`, {
    method: "POST",
    body: JSON.stringify({
      date_filter: dateFrom || null,
      date_to: dateTo || null,
      naics_codes: naicsCodes,
      award_notice: awardNotice,
    }),
  });
}

export function stopSamScrape(runId: string): Promise<{ success: boolean; message: string }> {
  return request(`/sam/scrape/stop/${runId}`, { method: "POST" });
}

export function getSamScreenshot(runId: string): Promise<{ screenshot: string }> {
  return request(`/sam/screenshot/${runId}`);
}

/**
 * A live browser frame for any portal's in-flight run (base64 PNG), or null
 * until one is available. Backs the Live Preview modal across all scrapers.
 */
export function getRunScreenshot(runId: string): Promise<{ screenshot: string | null }> {
  return request(`/runs/${runId}/screenshot`);
}

// -- Unison Marketplace ------------------------------------------------------

export function startUnisonScrape(
  filterBy: string,
  livePreview = false,
): Promise<{ run_id: string; search: string; folder: string }> {
  return request(`/unison/scrape${livePreviewQuery(livePreview)}`, {
    method: "POST",
    body: JSON.stringify({ filter_by: filterBy || null }),
  });
}

// -- NAICS reference tool ----------------------------------------------------

export interface NaicsResult {
  code: string;
  title: string;
}

export interface NaicsListResponse {
  total: number;
  page: number;
  limit: number;
  results: NaicsResult[];
}

export function getNaicsCodes(q: string, page: number, limit = 50): Promise<NaicsListResponse> {
  const params = new URLSearchParams({ q, page: String(page), limit: String(limit) });
  return request(`/naics?${params.toString()}`);
}

export function startNaicsScrape(): Promise<{ run_id: string }> {
  return request("/naics/scrape", { method: "POST" });
}

// -- shared ------------------------------------------------------------------

export function getRunStatus(portal: Portal, runId: string): Promise<RunStatus> {
  return request(`/${portal}/scrape/status/${runId}`);
}

export function listRuns(portal: Portal): Promise<{ runs: RunStatus[] }> {
  return request(`/${portal}/scrape/runs`);
}

/**
 * Direct link to BidNet's on-demand Excel of every stored solicitation. It is a
 * file download, so it has to be a real href rather than a fetch. BidNet is the
 * only portal exposing `/export`.
 */
export function bidnetExportUrl(): string {
  return `${API_URL}/bidnet/export`;
}

/**
 * A completed run's archive ZIP as a browser download: the cumulative Excel
 * report plus every downloaded bid document in its niche-wise folder.
 */
export function runDownloadUrl(runId: string): string {
  return `${API_URL}/runs/${runId}/download`;
}

/** FastAPI's generated interactive reference, served by the backend at /docs. */
export function apiDocsUrl(): string {
  return `${API_URL}/docs`;
}
