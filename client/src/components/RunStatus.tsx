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
  if (step.startsWith("downloading_documents:")) return `Downloading documents for bid ${step.split(":")[1]}`;
  if (step.startsWith("opening_opportunity:")) return `Opening opportunity ${step.split(":")[1]}`;
  if (step.startsWith("downloading_zip:")) return `Downloading documents (${step.split(":")[1]})`;
  return STEP_LABELS[step] ?? step;
}

function runSubtitle(run: RunStatusData): string {
  if (run.category_label) return run.category_label;
  if (run.scraper === "bidnet") return run.keyword ? `“${run.keyword}”` : "BidNet Direct";
  if (run.scraper === "ridemetro") return "RideMetro";
  if (run.scraper === "myflorida") return "MyFlorida";
  return "";
}

const STATUS_STYLE: Record<string, string> = {
  pending: "border-slate-500/30 bg-slate-500/10 text-slate-300",
  running: "border-sky-400/30 bg-sky-400/10 text-sky-300",
  completed: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
  failed: "border-red-400/30 bg-red-400/10 text-red-300",
};

export default function RunStatus({ run }: { run: RunStatusData }) {
  return (
    <div className="space-y-4 rounded-2xl border border-white/10 bg-slate-950/40 p-5 backdrop-blur-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className={`rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${STATUS_STYLE[run.status]}`}>
            {run.status}
          </span>
          <span className="text-sm text-slate-400">{runSubtitle(run)}</span>
        </div>
        <span className="font-mono text-[11px] text-slate-600">run:{run.run_id}</span>
      </div>

      {/* Terminal-style live step */}
      {(run.status === "running" || run.status === "pending") && (
        <div className="flex items-center gap-2 rounded-lg border border-white/5 bg-black/30 px-3 py-2 font-mono text-xs text-emerald-300">
          <span className="text-emerald-500">$</span>
          <span>{stepLabel(run.step)}</span>
          <span className="caret-blink text-emerald-400">▋</span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        <Stat label="Bids found" value={run.bids_found} />
        <Stat label="Processed" value={run.bids_processed} />
        <Stat label="Documents" value={run.documents_downloaded} accent />
        <Stat label="Excel" value={run.excel_exported ? "saved" : "—"} />
      </div>

      {run.errors.length > 0 && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/[0.06] p-3">
          <p className="mb-1 font-mono text-[11px] font-semibold uppercase tracking-wide text-red-400">
            errors ({run.errors.length})
          </p>
          <ul className="max-h-24 space-y-0.5 overflow-y-auto text-xs text-red-300/90">
            {run.errors.map((error, i) => (
              <li key={i} className="font-mono">
                {error}
              </li>
            ))}
          </ul>
        </div>
      )}

      {run.status === "completed" && (
        <p className="truncate font-mono text-[11px] text-slate-500">
          <span className="text-slate-600">output ›</span> {run.folder}
        </p>
      )}
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: string | number; accent?: boolean }) {
  return (
    <div className="rounded-xl border border-white/5 bg-white/[0.02] p-3 text-center">
      <div className={`text-xl font-bold ${accent ? "text-emerald-400" : "text-white"}`}>{value}</div>
      <div className="mt-0.5 font-mono text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
    </div>
  );
}
