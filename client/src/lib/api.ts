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
  | "emma"
  | "philadelphia";

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
  // RideMetro — `agency` (shared with Wisconsin) is which Euna Supplier Network
  // agency the opportunity came from; `close_date` / `days_left` are that
  // agency's own list columns.
  ref_number?: string;
  project?: string;
  days_left?: string;
  // BidNet. `status` (shared with North Dakota) is the record-completeness flag:
  // OK | PARTIAL_DATA | EXTRACTION_FAILED — a bid whose detail page could not be
  // read is exported flagged rather than dropped.
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
  // SEPTA Open Bids. The Bid module's rows key on a bid number and carry a
  // title, where the Quote module's carry a requisition number and a summary —
  // which is why a run reports one module's shape or the other's, never a mix.
  bid_number?: string;
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
  // MyFlorida ad-status sweep. A current run captures every ad and keeps its
  // attachments, so a row says how many were saved and which folder of the
  // archive they are in. `niche`/`score`/`strength` are the classifier's verdict
  // and appear only on runs recorded before the matrix was taken out of the
  // pipeline — see MyFloridaSweepResults, which renders whichever it is given.
  document_count?: number;
  folder?: string;
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

/** One row of the RideMetro run's Euna Supplier Network roster. */
export interface RideMetroAgency {
  name: string;
  url: string;
  /** The agency's supplier-registration status: "Complete" | "Incomplete". */
  status: string;
  /** True when the run skipped it — registration Incomplete, so no portal. */
  skipped: boolean;
  opportunities: number;
  error?: string | null;
}

/** One row of the Active Jobs panel: a run on any portal, trimmed to what the
 *  panel shows. Deliberately not the whole run — that carries every scraped bid.
 *  Served by `GET /runs`, which spans every portal in one request. */
export interface Job {
  run_id: string;
  scraper: Portal | typeof SWEEP_SCRAPER;
  status: RunStatus["status"];
  step: string;
  started_at: string;
  finished_at: string | null;
  bids_found: number;
  bids_processed: number;
  documents_downloaded: number;
  /** 0 while running; 1 = next in line, 2 = behind one other, and so on. */
  queue_position?: number;
  errors: number;
  warnings: number;
  /** The most recent log line's sequence number, for the tail poller. */
  log_seq: number;
  // Whichever of these the owning portal sets — the job's subtitle.
  label?: string;
  search?: string;
  account_label?: string;
  niche_label?: string;
  filter_label?: string;
  module?: string;
}

/** How many runs are executing, waiting, and allowed at once. */
export interface JobCapacity {
  running: number;
  queued: number;
  capacity: number;
}

export interface JobLogLine {
  seq: number;
  ts: number | null;
  level: string;
  logger: string;
  message: string;
}

export function getJobs(activeOnly = true): Promise<{ jobs: Job[]; capacity: JobCapacity }> {
  return request(`/runs?active=${activeOnly}`);
}

export function getJobLogs(runId: string, after = 0): Promise<{ lines: JobLogLine[]; seq: number }> {
  return request(`/runs/${runId}/logs?after=${after}`);
}

export interface RunStatus {
  run_id: string;
  // The sweep runs under its own key rather than a `Portal` — see the sweep
  // section below for why it isn't one.
  scraper?: Portal | typeof SWEEP_SCRAPER;
  // queued = accepted and waiting for a slot in the scrape pool
  // (app/core/jobs.py); it has not started and no browser exists yet.
  status: "pending" | "queued" | "running" | "completed" | "failed" | "stopped";
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
  // SEPTA-only: which module the run searched, and the optional filters it was
  // launched with. `module` decides which columns the results table renders.
  module?: SeptaModule;
  date_filter?: string | null;
  commodity_code?: string | null;
  // SEPTA-only: the always-on close-date filter's effect. Only quotes closing at
  // least `min_days_until_close` days out are kept; these tally what it dropped
  // (closing sooner) and kept-but-couldn't-verify (unreadable close date).
  min_days_until_close?: number;
  bids_skipped_closing_soon?: number;
  bids_kept_unreadable_close?: number;
  // RideMetro-only: the Euna Supplier Network sweep. `agencies` is the whole My
  // Network roster in portal order — including the Incomplete ones the run
  // skipped and the Complete ones that had nothing open, neither of which
  // contributes a bid row. `bids_closing_soon` counts (but does not drop)
  // opportunities closing within the hub's 7-day runway.
  agencies?: RideMetroAgency[];
  agencies_found?: number;
  agencies_scraped?: number;
  bids_closing_soon?: number;
  /** Which login the run used — the two accounts sweep different networks, so a
   *  finished run has to say which one it was. Named, never addressed. */
  account?: string;
  account_label?: string;
  // BidNet: how the run's records broke down by completeness.
  bids_fully_extracted?: number;
  bids_partial?: number;
  bids_extraction_failed?: number;
  // SAM-only filters.
  date_from?: string | null;
  date_to?: string | null;
  naics_codes?: string[];
  award_notice?: boolean;
  // Unison-only. No longer set by new runs — a run takes no parameters and the
  // scraper's filters are all off for testing (app/scrapers/unison/filters.py).
  // Kept so runs recorded before that still render their filter in the history.
  filter_by?: string | null;
  /** The portal's Filter By criterion this run used: the option value and its
   *  label. `-1` / "Select Criteria" means the whole listing. */
  filter_id?: string;
  filter_label?: string;
  pages_scraped?: number;
  /** How many bids the portal itself said the listing held, read from its
   *  "1 - 100 of 115 Buys" line. `bids_found` short of this means the walk
   *  missed pages — the run records an error saying so. */
  bids_detected?: number;
  /** How the run's buys came out of the evaluator, e.g. `{PURSUE: 4, REJECT: 20}`. */
  decisions?: Record<string, number>;
  /** Unison: bids rejected by an early-exit screen (GSA Schedules, hospitality
   *  and food) before the evaluation matrix ran, and bids the strict fallback
   *  decided instead of sending to manual review. */
  screened_out?: number;
  manual_review_resolved?: number;
  /** Which of the Unison scraper's filters ran, and a one-line rendering of
   *  them. The keyword and close-date filters stay off for the testing phase. */
  filters_active?: Record<string, boolean>;
  // BidNet-only: which niche the run is searching, and how many terms it
  // owns. A niche searches its keywords and then its NIGP codes, one term per
  // search, so `search_count` is the total and `keyword`/`keyword_progress`
  // track the one being searched now.
  niche_label?: string;
  keyword_count?: number;
  nigp_count?: number;
  search_count?: number;
  /** Keywords the portal reported zero bids for — skipped without waiting on
   *  results that were never coming. */
  keywords_without_results?: string[];
  // BidNet batch runs (POST /bidnet/scrape/batch): one execution over several
  // niches, run one at a time. The parent run carries the progress; each niche
  // has its own run id, status and ZIP in `niche_results`.
  is_batch?: boolean;
  niche_total?: number;
  niche_done?: number;
  niche_current?: string;
  niches_requested?: string[];
  niches_completed?: number;
  niches_failed?: number;
  niche_results?: BidnetNicheResult[];
  // BidNet-only: the sidebar filters this run was launched with, and a one-line
  // rendering of them for the status panel.
  filters?: BidnetFilters;
  filters_summary?: string;
  // BidNet filter-option discovery runs: how many options each panel yielded.
  filter_option_counts?: Record<string, number>;
  /** MyFlorida: true while the run is parked at the one-time password, waiting
   *  for someone to type it into the open browser window. `otp_wait_seconds` is
   *  how long it will wait before giving up. */
  awaiting_otp?: boolean;
  otp_wait_seconds?: number;
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

/** One of the two RideMetro logins a run can use. Separate accounts belong to
 *  separate Euna Supplier Networks, so the choice decides which agencies get
 *  swept — not just who signs in. Carries no credentials and no login address:
 *  an account is identified by its label. `configured` is false when either key
 *  is missing from the server's .env, which is what makes it unselectable, and
 *  the `*_env` names say which keys to fill in. */
export interface RideMetroAccount {
  key: string;
  label: string;
  configured: boolean;
  username_env: string;
  password_env: string;
}

export function getRideMetroAccounts(): Promise<{ accounts: RideMetroAccount[]; default: string }> {
  return request("/ridemetro/accounts");
}

export function startRideMetroScrape(
  account: string,
  livePreview = false,
): Promise<{ run_id: string; folder: string; account: string }> {
  const query = new URLSearchParams({ account });
  if (livePreview) query.set("live_preview", "true");
  return request(`/ridemetro/scrape?${query}`, { method: "POST" });
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
  /** Free text typed into BidNet's own Keywords panel — terms to drop from the
   *  results. Comma, semicolon or newline separated; spaces do not separate, so
   *  a multi-word entry stays one phrase. Blank leaves the panel untouched. */
  excluded_keywords?: string;
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
  /** Its NIGP class-item / UNSPSC codes, searched the same way after them. */
  nigp_count?: number;
  /** Keywords plus codes — the number of searches a run actually makes. */
  search_count?: number;
}

export function getBidnetNiches(): Promise<{ niches: BidnetNiche[] }> {
  return request("/bidnet/niches");
}

/**
 * Start a run over one niche. The backend looks up that niche's keywords and
 * NIGP codes and searches each separately in a single session — never combined
 * into one boolean query, which would return only the bids matching every term.
 * A solicitation reached by several terms is exported once, naming them all.
 */
/** One niche of a batch, as the parent run reports it. */
export interface BidnetNicheResult {
  niche: string;
  label: string;
  /** completed | failed | stopped — the niche's own run status. */
  status: string;
  run_id?: string;
  bids?: number;
  zip_name?: string | null;
  error?: string;
}

export function startBidnetScrape(
  niche: string,
  filters: BidnetFilters = {},
  livePreview = false,
): Promise<{
  run_id: string;
  niche: string;
  niche_label: string;
  keyword_count: number;
  nigp_count?: number;
  search_count?: number;
  folder: string;
  filters: BidnetFilters;
}> {
  return request(`/bidnet/scrape${livePreviewQuery(livePreview)}`, {
    method: "POST",
    body: JSON.stringify({ niche, filters }),
  });
}

/**
 * Run several niches in one execution, sequentially. Each niche gets its own
 * browser session, its own output folder and its own ZIP — nothing is shared
 * between them — and one niche failing does not stop the rest.
 *
 * Omit `niches` to run every niche in the catalog. Returns the batch's run id,
 * which polls like any other run; `niche_results` on it names each niche's own
 * run id as it finishes.
 */
export function startBidnetBatch(
  niches?: string[],
  filters: BidnetFilters = {},
  livePreview = false,
): Promise<{
  batch_id: string;
  workspace: string;
  niches: { key: string; label: string; keyword_count: number; nigp_count: number; search_count: number }[];
  filters: BidnetFilters;
}> {
  return request(`/bidnet/scrape/batch${livePreviewQuery(livePreview)}`, {
    method: "POST",
    body: JSON.stringify(niches?.length ? { niches, filters } : { filters }),
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

/**
 * Which of the portal's two modules a run searches. Exactly one — the run
 * navigates to it and searches it, and never opens the other.
 */
export type SeptaModule = "quotes" | "open_bids";

export const SEPTA_MODULES: readonly { value: SeptaModule; label: string; hint: string }[] = [
  {
    value: "quotes",
    label: "Open Quotes",
    hint: "The Quote module — parts requisitions, keyed by requisition number.",
  },
  {
    value: "open_bids",
    label: "Open Bids",
    hint: "The Bid module — solicitations, keyed by bid number.",
  },
];

export interface StartSeptaScrapeOptions {
  /** Which module to search. Defaults to Open Quotes, as the API does. */
  module?: SeptaModule;
  /** Open Date Range "from", YYYY-MM-DD. Optional; there is no "to" bound. */
  dateFrom?: string;
  livePreview?: boolean;
}

/** Start a SEPTA run against one module. The opens-from date is optional and
 *  has no default — omitting it fetches every open row in the selected module,
 *  which is the normal case. */
export function startSeptaScrape({
  module = "quotes",
  dateFrom = "",
  livePreview = false,
}: StartSeptaScrapeOptions = {}): Promise<{
  run_id: string;
  search: string;
  module: SeptaModule;
  folder: string;
}> {
  return request(`/septa/scrape${livePreviewQuery(livePreview)}`, {
    method: "POST",
    body: JSON.stringify({ module, date_from: dateFrom || null }),
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

/** One option of the portal's own "Filter By" dropdown. `value` is what the
 *  scraper selects on the page (`-1` = "Select Criteria", i.e. no filter). */
export interface UnisonFilter {
  value: string;
  label: string;
}

export function getUnisonFilters(): Promise<{ filters: UnisonFilter[]; default: string }> {
  return request("/unison/filters");
}

/** Start a Unison run. The only choice is the portal's Filter By criterion —
 *  everything else about how a run is narrowed lives server-side in
 *  app/scrapers/unison/filters.py. */
export function startUnisonScrape(
  filterId: string,
  livePreview = false,
): Promise<{ run_id: string; search: string; filter_id: string; folder: string }> {
  const query = new URLSearchParams({ filter_id: filterId });
  if (livePreview) query.set("live_preview", "true");
  return request(`/unison/scrape?${query}`, { method: "POST" });
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
  // These are the three filters the portal shows above the results grid.
  keyword?: string;
  status?: string;
  category?: string;
  livePreview?: boolean;
}

/**
 * Sign in, open Public Solicitations, optionally apply the filter bar (Keywords /
 * Status / Category), scrape the whole grid, open each solicitation to extract
 * all its fields and download its documents, and store every solicitation still
 * closing at least 7 days out.
 */
export function startEmmaScrape({
  keyword = "",
  status = "",
  category = "",
  livePreview = false,
}: StartEmmaScrapeOptions = {}): Promise<{ run_id: string; search: string; folder: string }> {
  return request(`/emma/scrape${livePreviewQuery(livePreview)}`, {
    method: "POST",
    body: JSON.stringify({ keyword, status, category }),
  });
}

// -- City of Philadelphia (PHLContracts) -------------------------------------

/**
 * Advanced Search criteria for a PHLContracts run. Every field is optional and
 * an empty one is not a filter — the server drops blanks, so a half-filled form
 * narrows by exactly the fields that were filled.
 *
 * Dropdown criteria take either the portal's own code or the text it shows
 * ("MI" or "Micro Purchase"), which is what lets this form stay in the words of
 * the person filling it in.
 */
export interface PhiladelphiaFilters {
  description?: string;
  item_description?: string;
  bid_number?: string;
  alternate_id?: string;
  buyer?: string;
  organization?: string;
  department?: string;
  nigp_class?: string;
  type_code?: string;
  status?: string;
  category?: string;
  opening_date_from?: string;
  opening_date_to?: string;
  /** Off: every filled criterion must match. On: any one of them may. */
  match_any?: boolean;
}

/**
 * Start a run on PHLContracts.
 *
 * With no filters this walks the whole Open Bids list — the portal's full
 * published scope. With filters it drives the portal's own Advanced Search
 * (Document Type: Bid Solicitations) and takes what that returns. Both paths
 * end at the same results table, so the rest of the run is identical: each
 * bid's detail page is read, its attachments are saved into a folder of its
 * own, and the lot is packaged into one ZIP with a summary sheet at its root.
 */
export function startPhiladelphiaScrape(
  livePreview = false,
  filters?: PhiladelphiaFilters,
): Promise<{ run_id: string; search: string; folder: string; filters: PhiladelphiaFilters }> {
  return request(`/philadelphia/scrape${livePreviewQuery(livePreview)}`, {
    method: "POST",
    body: JSON.stringify(filters ?? {}),
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
