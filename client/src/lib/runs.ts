import { listRuns, type Portal, type RunStatus } from "@/lib/api";

export interface Totals {
  runs: number;
  bids: number;
  documents: number;
  /** Sources that answered — the landing only claims what it can verify. */
  sourcesUp: number;
}

/**
 * Totals across every portal, for the landing page. Portals are queried in
 * parallel and failures are skipped rather than thrown: the landing must still
 * render if the API is down, just without the numbers.
 *
 * Resolves to null when no portal answered at all.
 */
export async function fetchTotals(portals: readonly Portal[]): Promise<Totals | null> {
  const results = await Promise.allSettled(portals.map((p) => listRuns(p)));
  const ok = results.filter((r) => r.status === "fulfilled");
  if (ok.length === 0) return null;

  const runs = ok.flatMap((r) => r.value.runs ?? []);
  return {
    runs: runs.length,
    bids: runs.reduce((sum, r) => sum + (r.bids_found ?? 0), 0),
    documents: runs.reduce((sum, r) => sum + (r.documents_downloaded ?? 0), 0),
    sourcesUp: ok.length,
  };
}

export interface RunsState {
  runs: RunStatus[] | null;
  error: string | null;
  loading: boolean;
}

export const RUNS_LOADING: RunsState = { runs: null, error: null, loading: true };

/**
 * Loads a portal's run list and resolves to the next view state — it never
 * throws and never sets state itself, so callers can apply the result inside a
 * `.then()` and keep effects free of synchronous state updates.
 *
 * `what` names the view in the error message ("run history", "exports").
 */
export async function fetchRunsState(portal: Portal, what: string): Promise<RunsState> {
  try {
    const data = await listRuns(portal);
    return { runs: sortByNewest(data.runs ?? []), error: null, loading: false };
  } catch (e) {
    return {
      runs: [],
      error: `Could not load ${what} — is the API running? (${(e as Error).message})`,
      loading: false,
    };
  }
}

/** `2026-07-17T00:29:11` → `17 Jul 2026, 00:29`. */
export function formatTimestamp(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Wall-clock length of a run, or "—" while it is still going. */
export function formatDuration(run: RunStatus): string {
  if (!run.started_at || !run.finished_at) return "—";
  const ms = new Date(run.finished_at).getTime() - new Date(run.started_at).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}

/** Newest first — the backend does not guarantee ordering. */
export function sortByNewest(runs: RunStatus[]): RunStatus[] {
  return [...runs].sort(
    (a, b) => new Date(b.started_at ?? 0).getTime() - new Date(a.started_at ?? 0).getTime(),
  );
}

/**
 * Short human label for what a run was searching for.
 *
 * Falls back to describing the portal's default sweep rather than the run's
 * internal label, which is only a timestamp and tells the reader nothing.
 */
export function runTarget(run: RunStatus): string {
  if (run.category_label) return run.category_label;

  // Wisconsin's `search` is already a composed summary ("keyword=x, nigp=915",
  // or "all current solicitations"), so use it rather than rebuilding one.
  if (run.scraper === "wisconsin") {
    const search = run.search?.trim();
    if (!search || search === "all current solicitations") return "All current solicitations";
    return search;
  }
  if (run.scraper === "northdakota") {
    const search = run.search?.trim();
    if (!search || search === "all public solicitations") return "All public solicitations";
    return search;
  }
  if (run.scraper === "ridemetro") return "All open opportunities";
  if (run.scraper === "septa") {
    return run.date_filter ? `Opens ${run.date_filter}` : "Today's open quotes";
  }
  if (run.scraper === "sam") {
    const search = run.search?.trim();
    return !search || search === "all active solicitations" ? "All active solicitations" : search;
  }
  if (run.scraper === "unison") return run.filter_by?.trim() || "All buyer requests";
  if (run.scraper === "naics") return "NAICS reference refresh";

  if (run.keywords?.length) return run.keywords.join(", ");
  if (run.keyword) return run.keyword;
  return "—";
}

/** Portals with nothing to download (reference data / login-only). */
const NO_DOWNLOAD = new Set(["naics", "caleprocure", "evalconfig"]);

/** True when this portal's runs produce a downloadable archive ZIP. */
export function portalDownloadable(portal: string): boolean {
  return !NO_DOWNLOAD.has(portal);
}

/**
 * True when `GET /runs/{id}/download` will serve this run's archive ZIP —
 * the cumulative Excel report plus all downloaded bid documents. Null-ish
 * (false) while the run hasn't completed or for portals with no results.
 */
export function runDownloadable(run: RunStatus): boolean {
  return run.status === "completed" && portalDownloadable(run.scraper ?? "");
}
