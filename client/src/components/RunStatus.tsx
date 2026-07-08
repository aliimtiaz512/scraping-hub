"use client";

import type { RunStatus as RunStatusData } from "@/lib/api";

const STEP_LABELS: Record<string, string> = {
  queued: "Queued",
  logging_in: "Logging in",
  // MyFlorida
  opening_advertisements: "Opening Advertisements",
  opening_advanced_search: "Opening Advanced Search",
  entering_commodity_codes: "Entering commodity codes",
  searching: "Running search",
  collecting_bids: "Collecting bid list",
  exporting_excel: "Exporting Excel",
  storing_in_db: "Storing bids in database",
  // RideMetro
  opening_opportunities: "Opening opportunities list",
  scraping_project_details: "Scraping project details",
  generating_excel: "Generating Excel from database",
  // BidNet
  filtering_member_agency: "Filtering to Member Agency Bids",
  opening_bid: "Opening solicitation",
  done: "Done",
  failed: "Failed",
};

function stepLabel(step: string): string {
  if (step.startsWith("downloading_documents:")) {
    return `Downloading documents for bid ${step.split(":")[1]}`;
  }
  if (step.startsWith("opening_opportunity:")) {
    return `Opening opportunity ${step.split(":")[1]}`;
  }
  if (step.startsWith("downloading_zip:")) {
    return `Downloading documents (${step.split(":")[1]})`;
  }
  return STEP_LABELS[step] ?? step;
}

function runSubtitle(run: RunStatusData): string {
  if (run.category_label) return run.category_label;
  if (run.scraper === "bidnet") return run.keyword ? `BidNet · “${run.keyword}”` : "BidNet Direct";
  if (run.scraper === "ridemetro") return "RideMetro";
  if (run.scraper === "myflorida") return "MyFlorida";
  return "";
}

const STATUS_STYLE: Record<string, string> = {
  pending: "bg-slate-200 text-slate-700",
  running: "bg-blue-100 text-blue-800",
  completed: "bg-emerald-100 text-emerald-800",
  failed: "bg-red-100 text-red-800",
};

export default function RunStatus({ run }: { run: RunStatusData }) {
  return (
    <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold uppercase ${STATUS_STYLE[run.status]}`}>
            {run.status}
          </span>
          <span className="text-sm text-slate-600">{runSubtitle(run)}</span>
        </div>
        <span className="font-mono text-xs text-slate-400">run {run.run_id}</span>
      </div>

      {run.status === "running" && (
        <div className="flex items-center gap-2 text-sm text-slate-700">
          <span className="h-2 w-2 animate-pulse rounded-full bg-blue-600" />
          {stepLabel(run.step)}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Bids found" value={run.bids_found} />
        <Stat label="Bids processed" value={run.bids_processed} />
        <Stat label="Documents" value={run.documents_downloaded} />
        <Stat label="Excel export" value={run.excel_exported ? "saved" : "—"} />
      </div>

      {run.errors.length > 0 && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3">
          <p className="mb-1 text-xs font-semibold text-red-800">Errors ({run.errors.length})</p>
          <ul className="max-h-24 space-y-0.5 overflow-y-auto text-xs text-red-700">
            {run.errors.map((error, i) => (
              <li key={i}>{error}</li>
            ))}
          </ul>
        </div>
      )}

      {run.status === "completed" && (
        <p className="text-xs text-slate-500">
          Output folder: <span className="font-mono">{run.folder}</span>
        </p>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md bg-slate-50 p-2 text-center">
      <div className="text-lg font-semibold text-slate-900">{value}</div>
      <div className="text-xs text-slate-500">{label}</div>
    </div>
  );
}
