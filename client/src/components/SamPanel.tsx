"use client";

import { useState } from "react";

import NaicsFileImport from "@/components/NaicsFileImport";
import NaicsSelect from "@/components/NaicsSelect";
import RunStatusPanel from "@/components/RunStatus";
import SamResults from "@/components/SamResults";
import { Card, ErrorBanner, LaunchBar, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import RefreshButton from "@/components/RefreshButton";
import ScraperControls from "@/components/ScraperControls";
import SessionActivity from "@/components/SessionActivity";
import { usePortalSession } from "@/lib/usePortalSession";
import { isRunActive, startSamScrape } from "@/lib/api";

const inputClass =
  "w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm transition placeholder:text-ink-400 focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/25 disabled:cursor-not-allowed disabled:bg-ink-50 disabled:text-ink-400";

export default function SamPanel() {
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [naicsCodes, setNaicsCodes] = useState<string[]>([]);
  const [awardNotice, setAwardNotice] = useState(false);
  const [sourceSought, setSourceSought] = useState(false);

  // The run, its log tail, its error banner and the launching flag all live in
  // the global session registry — not here. That is what lets this panel be
  // unmounted and remounted (or simply hidden) without the scrape noticing.
  const { run, error, starting, setError, launch, applyStatus, hasSession, reset } =
    usePortalSession("sam");

  const handleStart = () =>
    launch(() =>
      startSamScrape({
        dateFrom: dateFrom.trim(),
        dateTo: dateTo.trim(),
        naicsCodes,
        awardNotice,
        sourceSought,
      }),
    );

  // Paused is still running as far as this panel is concerned: the form stays
  // locked and Start stays disabled until the run really ends.
  const isRunning = isRunActive(run);

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="Search filters"
        description="All optional. Narrow by SAM.gov updated-date range and NAICS code; leave blank to sweep every active solicitation. Each scraped bid is evaluated automatically."
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-xs font-semibold text-ink-700">Updated from</label>
            <input type="date" value={dateFrom} disabled={isRunning} onChange={(e) => setDateFrom(e.target.value)} className={inputClass} />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-semibold text-ink-700">Updated to</label>
            <input type="date" value={dateTo} disabled={isRunning} onChange={(e) => setDateTo(e.target.value)} className={inputClass} />
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1.5 block text-xs font-semibold text-ink-700">NAICS codes</label>
            <NaicsSelect selected={naicsCodes} onChange={setNaicsCodes} disabled={isRunning} />
            <NaicsFileImport onCodes={setNaicsCodes} disabled={isRunning} />
            <p className="mt-1.5 text-xs text-ink-500">
              Type part of a code or an industry name and pick from the catalogue, or upload a
              spreadsheet of them. Leave empty to search every NAICS.
            </p>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-5">
          <label className="flex items-center gap-2 text-sm text-ink-700">
            <input type="checkbox" checked={awardNotice} disabled={isRunning} onChange={(e) => setAwardNotice(e.target.checked)} className="h-4 w-4 rounded border-ink-300 text-indigo-600 focus:ring-indigo-400" />
            Include Award Notices
          </label>
          <label className="flex items-center gap-2 text-sm text-ink-700">
            <input type="checkbox" checked={sourceSought} disabled={isRunning} onChange={(e) => setSourceSought(e.target.checked)} className="h-4 w-4 rounded border-ink-300 text-indigo-600 focus:ring-indigo-400" />
            Include Source Sought
          </label>
        </div>
      </Card>

      <LaunchBar summary="Each bid is scored PURSUE / REJECT by the evaluator as it is scraped.">
        <div className="flex items-center gap-2">
          <StopButton run={run} onError={setError} />
          <LiveMonitor run={run} portal="sam" />
          <ScraperControls
            run={run}
            onError={setError}
            onStatusChange={applyStatus}
          />
          <RefreshButton run={run} hasSession={hasSession} onReset={reset} />
          <StartButton onClick={() => handleStart()} disabled={starting || isRunning} running={isRunning} starting={starting}>
            Start scrape
          </StartButton>
        </div>
      </LaunchBar>

      {run && <RunStatusPanel run={run} />}
      {/* The run's log, streaming into the panel that launched it. */}
      <SessionActivity portal="sam" />

      {run && <SamResults bids={run.bids} />}
    </div>
  );
}
