"use client";

import { runDownloadUrl, type RunStatus as RunStatusData } from "@/lib/api";
import { LinkButton, RunBadge } from "@/components/ui";
import { runDownloadable } from "@/lib/runs";

const STEP_LABELS: Record<string, string> = {
  queued: "Queued",
  logging_in: "Logging in",
  // MyFlorida
  opening_advertisements: "Opening Advertisements",
  opening_advanced_search: "Opening Advanced Search",
  entering_commodity_codes: "Entering commodity codes",
  selecting_ad_status: "Selecting ad status",
  selecting_ad_type: "Selecting ad type",
  searching: "Running search",
  collecting_bids: "Collecting bid list",
  exporting_excel: "Exporting Excel",
  merging_workbook: "Merging results workbook",
  storing_in_db: "Storing bids in database",
  // RideMetro
  opening_opportunities: "Opening opportunities list",
  scraping_project_details: "Scraping project details",
  generating_excel: "Generating Excel from database",
  // BidNet
  filtering_member_agency: "Filtering to Member Agency Bids",
  opening_bid: "Opening solicitation",
  // North Dakota
  awaiting_manual_login: "Waiting for you to solve the CAPTCHA in the browser…",
  opening_solicitations: "Opening Solicitations menu",
  opening_public_solicitation_request: "Opening Public Solicitation Requests",
  // SAM / Unison / NAICS
  scraping: "Scraping",
  saving: "Saving to database",
  fetching_naics: "Fetching NAICS codes",
  scraping_results: "Scraping results grid",
  packaging_results: "Packaging results into ZIP",
  done: "Done",
  failed: "Failed",
};

function stepLabel(step: string): string {
  if (step.startsWith("downloading_documents:")) return `Downloading documents for bid ${step.split(":")[1]}`;
  if (step.startsWith("opening_opportunity:")) return `Opening opportunity ${step.split(":")[1]}`;
  if (step.startsWith("downloading_zip:")) return `Downloading documents (${step.split(":")[1]})`;
  // The keyword itself can contain no colon, so everything after the first is it.
  if (step.startsWith("entering_keyword:")) return `Searching title for “${step.slice("entering_keyword:".length)}”`;
  return STEP_LABELS[step] ?? step;
}

function runSubtitle(run: RunStatusData): string {
  // A keyword run works through one keyword at a time — show which.
  if (run.category_label && run.mode === "keywords" && run.keyword) {
    const progress = run.keyword_progress ? ` ${run.keyword_progress}` : "";
    return `${run.category_label} · “${run.keyword}”${progress}`;
  }
  if (run.category_label) return run.category_label;
  if (run.scraper === "bidnet") return run.keyword ? `“${run.keyword}”` : "BidNet Direct";
  if (run.scraper === "ridemetro") return "RideMetro";
  if (run.scraper === "myflorida") return "MyFlorida";
  if (run.scraper === "northdakota") return run.search && run.search !== "all public solicitations" ? run.search : "North Dakota";
  if (run.scraper === "septa") return run.date_filter ? `Opens ${run.date_filter}` : "SEPTA";
  if (run.scraper === "sam") return run.search && run.search !== "all active solicitations" ? run.search : "SAM.gov";
  if (run.scraper === "unison") return run.filter_by ? run.filter_by : "Unison Marketplace";
  if (run.scraper === "naics") return "NAICS refresh";
  return "";
}

export default function RunStatus({ run }: { run: RunStatusData }) {
  const inFlight = run.status === "running" || run.status === "pending";
  const subtitle = runSubtitle(run);
  const download = runDownloadable(run);

  return (
    <section className="overflow-hidden rounded-xl border border-ink-200 bg-white shadow-sm">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-100 px-5 py-3.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <h3 className="text-sm font-semibold text-ink-900">Run status</h3>
          {subtitle && <span className="truncate text-sm text-ink-500">{subtitle}</span>}
        </div>
        <div className="flex items-center gap-2.5">
          <span className="font-mono text-xs text-ink-400">{run.run_id}</span>
          <RunBadge status={run.status} />
        </div>
      </header>

      {/* Live step: the current action, with an indeterminate bar underneath.
          The backend reports steps, not percentages, so no fake completion %. */}
      {inFlight && (
        <div className="border-b border-ink-100 bg-ink-50/70 px-5 py-3.5">
          <div className="flex items-center gap-2.5">
            <span className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-gold-200 border-t-gold-600" />
            <span className="text-sm font-medium text-ink-700">{stepLabel(run.step)}</span>
          </div>
          <div className="mt-2.5 h-1 overflow-hidden rounded-full bg-ink-200">
            <div className="progress-slide h-full w-1/4 rounded-full bg-gold-500" />
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 divide-ink-100 sm:grid-cols-4 sm:divide-x">
        <Stat label="Bids found" value={run.bids_found} />
        <Stat label="Processed" value={run.bids_processed} />
        <Stat label="Documents" value={run.documents_downloaded} />
        <Stat label="Results" value={run.excel_exported ? "Ready" : "—"} muted={!run.excel_exported} />
      </div>

      {(run.errors.length > 0 || run.no_results || (run.warnings?.length ?? 0) > 0 || run.status === "completed") && (
        <div className="space-y-3 border-t border-ink-100 p-5">
          {run.errors.length > 0 && (
            <Notice tone="red" title={`${run.errors.length} ${run.errors.length === 1 ? "error" : "errors"}`}>
              <ul className="max-h-28 space-y-1 overflow-y-auto">
                {run.errors.map((error, i) => (
                  <li key={i} className="font-mono text-xs leading-relaxed">
                    {error}
                  </li>
                ))}
              </ul>
            </Notice>
          )}

          {run.no_results && (
            <Notice tone="amber" title="No matching ads">
              The search completed successfully — this niche currently has no ads matching your filters.
            </Notice>
          )}

          {run.warnings && run.warnings.length > 0 && (
            <Notice tone="amber" title={`No results for ${run.warnings.length} ${run.warnings.length === 1 ? "search" : "searches"}`}>
              <ul className="max-h-28 space-y-1 overflow-y-auto">
                {run.warnings.map((warning, i) => (
                  <li key={i} className="font-mono text-xs leading-relaxed">
                    {warning}
                  </li>
                ))}
              </ul>
            </Notice>
          )}

          {run.status === "completed" && download && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-ink-200 bg-ink-50 px-3 py-2.5">
              <div className="min-w-0">
                <p className="text-xs font-medium text-ink-600">Results ready</p>
                <p className="truncate text-xs text-ink-500">
                  Cumulative Excel report plus every downloaded bid document, bundled as one ZIP.
                </p>
              </div>
              <LinkButton
                href={runDownloadUrl(run.run_id)}
                variant="primary"
                size="sm"
                icon={
                  <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
                    <path d="M8 2v8m0 0L5 7m3 3l3-3M2.5 12.5h11" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                }
              >
                Download ZIP
              </LinkButton>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function Stat({ label, value, muted }: { label: string; value: string | number; muted?: boolean }) {
  return (
    <div className="border-b border-ink-100 px-5 py-4 sm:border-b-0">
      <div className="text-xs font-medium text-ink-500">{label}</div>
      <div className={`tabular mt-1 text-2xl font-semibold ${muted ? "text-ink-300" : "text-ink-900"}`}>{value}</div>
    </div>
  );
}

function Notice({
  tone,
  title,
  children,
}: {
  tone: "red" | "amber";
  title: string;
  children: React.ReactNode;
}) {
  const cls = {
    red: { box: "border-red-200 bg-red-50", head: "text-red-800", body: "text-red-700" },
    amber: { box: "border-amber-200 bg-amber-50", head: "text-amber-800", body: "text-amber-700" },
  }[tone];

  return (
    <div className={`rounded-lg border p-3.5 ${cls.box}`}>
      <p className={`mb-1 text-xs font-semibold ${cls.head}`}>{title}</p>
      <div className={`text-sm ${cls.body}`}>{children}</div>
    </div>
  );
}
