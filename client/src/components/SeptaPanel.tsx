"use client";

import { useState } from "react";

import RunStatusPanel from "@/components/RunStatus";
import SeptaResults from "@/components/SeptaResults";
import { Card, ErrorBanner, LaunchBar, SegmentedControl, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import RefreshButton from "@/components/RefreshButton";
import ScraperControls from "@/components/ScraperControls";
import SessionActivity from "@/components/SessionActivity";
import { usePortalSession } from "@/lib/usePortalSession";
import {
  SEPTA_MODULES,
  isRunActive,
  startSeptaScrape,
  type SeptaModule,
} from "@/lib/api";

export default function SeptaPanel() {
  const [module, setModule] = useState<SeptaModule>("quotes");
  const [dateFrom, setDateFrom] = useState("");
  // The run, its log tail, its error banner and the launching flag all live in
  // the global session registry — not here. That is what lets this panel be
  // unmounted and remounted (or simply hidden) without the scrape noticing.
  const { run, error, starting, setError, launch, applyStatus, hasSession, reset } =
    usePortalSession("septa");
  const handleStart = (livePreview = false) =>
    launch(() => startSeptaScrape({ module, dateFrom: dateFrom.trim(), livePreview }));

  // Paused is still running as far as this panel is concerned: the form stays
  // locked and Start stays disabled until the run really ends.
  const isRunning = isRunActive(run);
  const inputClass =
    "w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm transition placeholder:text-ink-400 focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/25 disabled:cursor-not-allowed disabled:bg-ink-50 disabled:text-ink-400";

  const openBids = module === "open_bids";
  const moduleName = openBids ? "Open Bids" : "Open Quotes";
  const rows = openBids ? "open bids" : "open quotes";

  const summary = dateFrom
    ? `${moduleName} opening from ${dateFrom} onward.`
    : `All ${rows} — no date filter.`;

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="Module"
        description="Which of the portal's two modules to search. Exactly one runs — the other is not opened."
      >
        <SegmentedControl
          name="septa-module"
          value={module}
          options={SEPTA_MODULES}
          onChange={setModule}
          disabled={isRunning}
        />
      </Card>

      <Card
        title="Opens from"
        description={`Optional. Leave blank to fetch every ${rows.slice(0, -1)}. Rows naming an out-of-scope manufacturer — in a ${openBids ? "bid's title" : "quote's summary"} — are skipped automatically.`}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-xs font-semibold text-ink-700">Opens from</label>
            <input
              type="date"
              value={dateFrom}
              disabled={isRunning}
              onChange={(e) => setDateFrom(e.target.value)}
              className={inputClass}
            />
            <p className="mt-1.5 text-xs text-ink-500">
              {dateFrom
                ? `${moduleName} opening on or after this date. No upper bound.`
                : `No date set: the search runs unfiltered and returns the whole ${moduleName} grid.`}
            </p>
          </div>
        </div>
      </Card>

      <LaunchBar summary={summary}>
        <div className="flex items-center gap-2">
          <StopButton run={run} onError={setError} />
          <LiveMonitor run={run} portal="septa" />
          <ScraperControls
            run={run}
            onError={setError}
            onStatusChange={applyStatus}
          />
          <RefreshButton run={run} hasSession={hasSession} onReset={reset} />
          <StartButton
            onClick={() => handleStart()}
            disabled={starting || isRunning}
            running={isRunning}
            starting={starting}
          >
            Search &amp; scrape
          </StartButton>
        </div>
      </LaunchBar>

      {run && <RunStatusPanel run={run} />}
      {/* The run's log, streaming into the panel that launched it. */}
      <SessionActivity portal="septa" />
      {/* The run's own module, not the toggle: flipping the selector while a
          run is in flight must not relabel the columns of rows already in. */}
      {run && <SeptaResults bids={run.bids} module={run.module} />}
    </div>
  );
}
