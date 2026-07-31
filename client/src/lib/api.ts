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
  | "naics"
  | "caleprocure"
  | "emma";

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
  // SEPTA (`niche` is shared with the MyFlorida sweep, declared below)
  requisition_number?: string;
  summary?: string;
  open_date?: string;
  /** Which of the niche's keywords/commodity codes surfaced this quote —
   *  comma-joined, since a search per term often finds the same one twice. */
  matched_terms?: string;
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
  decision?: string;   // PURSUE | REJECT | MANUAL_REVIEW (PENDING | ERROR only on eval failure)
  reason?: string;
  // Unison
  buyer_number?: string;
  buyer_description?: string;
  buyer?: string;
  end_date?: string;
  // MyFlorida ad-status sweep: the classifier's verdict for this ad. The sweep
  // reports rows itself rather than through the per-bid document crawl, so it
  // carries these instead of `documents`/`error` — see MyFloridaSweepResults.
  niche?: string;
  score?: number;
  strength?: string | null;
  // EMMA (eMaryland Marketplace Advantage) — reuses shared solicitation_type/status/close_date/title
  emma_id?: string;
  bpm_code?: string;
  publish_date?: string;
  main_category?: string;
  issuing_agency?: string;
  time_remaining?: string;
  award_status?: string;
  procurement_officer?: string;
  matched_filters?: string;
  // shared, but only from the flows that download documents per bid — optional
  // because the sweep does not.
  documents?: string[];
  error?: string | null;
  document_errors?: string[];
}

export interface RunStatus {
  run_id: string;
  // The sweep runs under its own key rather than a `Portal` — see the sweep
  // section below for why it isn't one.
  scraper?: Portal | typeof SWEEP_SCRAPER;
  status: "pending" | "running" | "completed" | "failed" | "stopped";
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
  // SEPTA-only: the always-on close-date filter's effect. Only quotes closing at
  // least `min_days_until_close` days out are kept; these tally what it dropped
  // (closing sooner) and kept-but-couldn't-verify (unreadable close date).
  min_days_until_close?: number;
  bids_skipped_closing_soon?: number;
  bids_kept_unreadable_close?: number;
  // SAM-only filters.
  date_from?: string | null;
  date_to?: string | null;
  naics_codes?: string[];
  award_notice?: boolean;
  // Unison-only.
  filter_by?: string | null;
  // BidNet-only: which niche the run is searching, and how many keywords it
  // owns. `keyword`/`keyword_progress` track the one being searched now.
  niche_label?: string;
  keyword_count?: number;
  /** Keywords the portal reported zero bids for — skipped without waiting on
   *  results that were never coming. */
  keywords_without_results?: string[];
  // BidNet-only: the sidebar filters this run was launched with, and a one-line
  // rendering of them for the status panel.
  filters?: BidnetFilters;
  filters_summary?: string;
  // BidNet filter-option discovery runs: how many options each panel yielded.
  filter_option_counts?: Record<string, number>;
  // Cal eProcure / EMMA: login-milestone diagnostics. `login_ok` is true once
  // the run has signed in and confirmed the session; `landing_*` describe the
  // page it ended on (the supplier homepage, or EMMA's Public Solicitations).
  login_ok?: boolean;
  landing_url?: string;
  landing_title?: string;
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

/** One selectable value in a sidebar filter panel. `value` is the portal's own
 *  `data-filter-item-value`, which is what the scraper writes back to it. */
export interface BidnetFilterOption {
  value: string;
  label: string;
}

/** A checkbox-list panel in BidNet's search sidebar. */
export interface BidnetFilterSection {
  /** Request field name — `locations`, `nigp_categories`, … */
  name: BidnetListFilterName;
  label: string;
  /** The portal's internal section key (`regionId`), shown for traceability. */
  section_key: string;
  /** Purchasing Group arrives fully selected; the user unselects from it. */
  default_all: boolean;
  /** True while the list is still only the ~12 options the sidebar renders
   *  inline — a refresh pass fills in the rest from "View All". */
  partial: boolean;
  options: BidnetFilterOption[];
}

export type BidnetListFilterName =
  | "nigp_categories"
  | "organizations"
  | "locations"
  | "purchasing_groups"
  | "solicitation_types"
  | "general_requirements";

export type BidnetDateFilterName = "published_date" | "closing_date";

/** A date panel: a set of mutually exclusive modes plus the "within" periods. */
export interface BidnetDateSection {
  name: BidnetDateFilterName;
  label: string;
  types: BidnetFilterOption[];
  within_options: BidnetFilterOption[];
}

export interface BidnetFilterCatalog {
  status: { options: BidnetFilterOption[]; default: string };
  sections: BidnetFilterSection[];
  dates: BidnetDateSection[];
  /** When the full option lists were last harvested from the portal; null while
   *  only the seeded catalog is available. */
  discovered_at: string | null;
}

/** One date panel's setting. Dates are mm/dd/yyyy, matching the portal's own
 *  datepicker format. */
export interface BidnetDateFilter {
  type: string;
  within?: string;
  day?: string;
  range_start?: string;
  range_end?: string;
}

/**
 * The sidebar state a run is launched with. Every list is optional and an empty
 * one means "leave that panel alone" — except `purchasing_groups`, where the
 * portal starts with all 52 ticked, so omitting it keeps them all and sending a
 * list narrows to it.
 */
export interface BidnetFilters {
  status?: string;
  nigp_categories?: string[];
  organizations?: string[];
  locations?: string[];
  purchasing_groups?: string[];
  solicitation_types?: string[];
  general_requirements?: string[];
  published_date?: BidnetDateFilter | null;
  closing_date?: BidnetDateFilter | null;
}

export function getBidnetFilters(): Promise<BidnetFilterCatalog> {
  return request("/bidnet/filters");
}

/** Kick off the browser pass that harvests every option from the portal's
 *  "View All" dialogs. Poll it like any other run. */
export function refreshBidnetFilterOptions(livePreview = false): Promise<{ run_id: string }> {
  return request(`/bidnet/filters/refresh${livePreviewQuery(livePreview)}`, { method: "POST" });
}

/** A BidNet business sector. Its keywords live server-side and are never sent
 *  to the client — a run selects a niche and the backend resolves the terms. */
export interface BidnetNiche {
  key: string;
  label: string;
  /** Filename-safe form of the label, used in the run folder name. */
  slug: string | null;
  /** How many keywords the niche searches, one search each. */
  keyword_count: number;
}

export function getBidnetNiches(): Promise<{ niches: BidnetNiche[] }> {
  return request("/bidnet/niches");
}

/**
 * Start a run over one niche. The backend looks up that niche's keywords and
 * searches each separately in a single session — never combined into one
 * boolean query, which would return only the bids matching every term.
 */
export function startBidnetScrape(
  niche: string,
  filters: BidnetFilters = {},
  livePreview = false,
): Promise<{
  run_id: string;
  niche: string;
  niche_label: string;
  keyword_count: number;
  folder: string;
  filters: BidnetFilters;
}> {
  return request(`/bidnet/scrape${livePreviewQuery(livePreview)}`, {
    method: "POST",
    body: JSON.stringify({ niche, filters }),
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

/** A niche owns the keywords and commodity codes a run searches, one per search.
 *  Seeded server-side from app/scrapers/septa/niches.py. */
export interface SeptaNiche {
  key: string;
  label: string;
  /** Filename-safe form of the label. */
  slug: string | null;
  keywords: string[];
  codes: string[];
  keyword_count: number;
  code_count: number;
}

export interface SeptaNicheCatalog {
  niches: SeptaNiche[];
}

export function getSeptaNiches(): Promise<SeptaNicheCatalog> {
  return request("/septa/niches");
}

export interface StartSeptaScrapeOptions {
  /** Catalog key. The scraper searches every keyword and commodity code this
   *  niche owns, then merges the results into one deduplicated sheet. */
  niche?: string;
  /** Optional extra filter; narrows every one of the niche's searches. */
  dateFilter?: string;
  livePreview?: boolean;
}

export function startSeptaScrape({
  niche = "",
  dateFilter = "",
  livePreview = false,
}: StartSeptaScrapeOptions): Promise<{ run_id: string; search: string; folder: string }> {
  return request(`/septa/scrape${livePreviewQuery(livePreview)}`, {
    method: "POST",
    body: JSON.stringify({
      niche: niche || null,
      date_filter: dateFilter || null,
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

// -- Cal eProcure (California eProcurement) -----------------------------------

/**
 * Cal eProcure currently supports login verification only — the run signs in and
 * confirms the session. It takes no search criteria yet; solicitation search and
 * export are added next.
 */
export function startCalEProcureScrape(): Promise<{ run_id: string; folder: string }> {
  return request("/caleprocure/scrape", { method: "POST" });
}

// -- EMMA (eMaryland Marketplace Advantage) -----------------------------------

export interface StartEmmaScrapeOptions {
  // All optional and combinable; all blank captures every public solicitation.
  category?: string;
  solicitationType?: string;
  status?: string;
  livePreview?: boolean;
}

/**
 * Sign in, open Public Solicitations, optionally apply the filter bar (Main
 * Category / Solicitation Type / Status), scrape the whole grid, and store every
 * solicitation still closing at least 7 days out.
 */
export function startEmmaScrape({
  category = "",
  solicitationType = "",
  status = "",
  livePreview = false,
}: StartEmmaScrapeOptions = {}): Promise<{ run_id: string; search: string; folder: string }> {
  return request(`/emma/scrape${livePreviewQuery(livePreview)}`, {
    method: "POST",
    body: JSON.stringify({ category, solicitation_type: solicitationType, status }),
  });
}

// -- shared ------------------------------------------------------------------

/** A run has reached a final state — the UI should stop polling it. */
export function isTerminalStatus(status: RunStatus["status"]): boolean {
  return status === "completed" || status === "failed" || status === "stopped";
}

/**
 * Stop an in-flight run, whichever scraper owns it. The shared endpoint routes
 * SAM to its cooperative stop and every other scraper to the run-state lock +
 * browser interrupt. 409 if the run already finished.
 */
export function stopScrape(runId: string): Promise<{ stopped: boolean; run_id: string }> {
  return request(`/runs/${runId}/stop`, { method: "POST" });
}

export function getRunStatus(portal: Portal, runId: string): Promise<RunStatus> {
  return request(`/${portal}/scrape/status/${runId}`);
}

// -- MyFlorida ad-status sweep -----------------------------------------------
// A second, independent way to run MyFlorida: no commodity codes and no
// keyword, just an ad status, with every advertisement found classified into
// one of six niches or Other. It lives under /myflorida/sweep rather than being
// a portal of its own, so it needs its own helpers instead of the `Portal`-keyed
// ones above.

/** The run key the sweep registers under — what `RunStatus.scraper` reports for
 *  a sweep run, and the only way to tell one apart from a niche run. */
export const SWEEP_SCRAPER = "myflorida_sweep";

export type AdStatusOption = "preview" | "open" | "closed" | "withdrawn";

export interface SweepNiche {
  key: string;
  label: string;
  sheet: string;
  order: number;
  core_terms: number;
  tier_a_codes: number;
}

export interface SweepNichesResponse {
  version: string;
  cross_listing: boolean;
  thresholds: Record<string, unknown>;
  niches: SweepNiche[];
  other_sheet: string;
  ad_statuses: AdStatusOption[];
}

export function getSweepNiches(): Promise<SweepNichesResponse> {
  return request("/myflorida/sweep/niches");
}

export function startMyFloridaSweep({
  adStatuses,
  maxBids = null,
  livePreview = false,
}: {
  adStatuses: AdStatusOption[];
  maxBids?: number | null;
  livePreview?: boolean;
}): Promise<{ run_id: string; search: string; folder: string }> {
  return request(`/myflorida/sweep/scrape${livePreviewQuery(livePreview)}`, {
    method: "POST",
    body: JSON.stringify({ ad_statuses: adStatuses, max_bids: maxBids }),
  });
}

export function getSweepRunStatus(runId: string): Promise<RunStatus> {
  return request(`/myflorida/sweep/scrape/status/${runId}`);
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
 * A completed run's results as a browser download: the archive ZIP holding the
 * cumulative Excel report plus every downloaded bid document in its niche-wise
 * folder — or, for portals that download no documents, the bare Excel report.
 */
export function runDownloadUrl(runId: string): string {
  return `${API_URL}/runs/${runId}/download`;
}

/** FastAPI's generated interactive reference, served by the backend at /docs. */
export function apiDocsUrl(): string {
  return `${API_URL}/docs`;
}
