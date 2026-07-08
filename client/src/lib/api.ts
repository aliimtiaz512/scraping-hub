const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Portal = "myflorida" | "ridemetro" | "bidnet";

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
  codes?: string[];
  excel_exported?: boolean;
  // RideMetro-only
  label?: string;
  excel_path?: string | null;
  // BidNet-only
  keyword?: string;
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

export function startMyFloridaScrape(
  category: string,
  priority: string,
): Promise<{ run_id: string; codes: string[]; folder: string }> {
  return request("/myflorida/scrape", {
    method: "POST",
    body: JSON.stringify({ category, priority }),
  });
}

// -- RideMetro ---------------------------------------------------------------

export function startRideMetroScrape(): Promise<{ run_id: string; folder: string }> {
  return request("/ridemetro/scrape", { method: "POST" });
}

// -- BidNet Direct -----------------------------------------------------------

export function startBidnetScrape(
  keyword: string,
): Promise<{ run_id: string; keyword: string; folder: string }> {
  return request("/bidnet/scrape", {
    method: "POST",
    body: JSON.stringify({ keyword }),
  });
}

// -- shared ------------------------------------------------------------------

export function getRunStatus(portal: Portal, runId: string): Promise<RunStatus> {
  return request(`/${portal}/scrape/status/${runId}`);
}

export function listRuns(portal: Portal): Promise<{ runs: RunStatus[] }> {
  return request(`/${portal}/scrape/runs`);
}
