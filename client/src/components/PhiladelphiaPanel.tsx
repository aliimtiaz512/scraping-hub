"use client";

import { useState } from "react";

import PhiladelphiaResults from "@/components/PhiladelphiaResults";
import PhiladelphiaSearch from "@/components/PhiladelphiaSearch";
import RunStatusPanel from "@/components/RunStatus";
import { ErrorBanner, LaunchBar, MiniButton, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import RefreshButton from "@/components/RefreshButton";
import ScraperControls from "@/components/ScraperControls";
import SessionActivity from "@/components/SessionActivity";
import { usePortalSession } from "@/lib/usePortalSession";
import {
  isRunActive,
  startPhiladelphiaScrape,
  type PhiladelphiaFilters,
} from "@/lib/api";

/**
 * PHLContracts: the whole Open Bids list, or a search of it.
 *
 * A run defaults to every open bid, which is the portal's full published scope
 * and needs nothing configured. Advanced Search is opt-in and hidden until
 * asked for, because a form that is always on the screen implies a run needs it
 * — and this one does not.
 */
export default function PhiladelphiaPanel() {
  // The run, its log tail, its error banner and the launching flag all live in
  // the global session registry — not here. That is what lets this panel be
  // unmounted and remounted (or simply hidden) without the scrape noticing.
  const { run, error, starting, setError, launch, applyStatus, hasSession, reset } =
    usePortalSession("philadelphia");
  const [advanced, setAdvanced] = useState(false);
  const [filters, setFilters] = useState<PhiladelphiaFilters>({});

  // Criteria only count while the panel is open: closing it is how you go back
  // to the whole list, and a filter still applying after it has been put away
  // would be a run doing something the screen does not show.
  const handleStart = (livePreview = false) =>
    launch(() => startPhiladelphiaScrape(livePreview, advanced ? filters : {}));

  // Paused is still running as far as this panel is concerned: the form stays
  // locked and Start stays disabled until the run really ends.
  const isRunning = isRunActive(run);
  const criteria = advanced
    ? Object.entries(filters).filter(([, v]) => v !== "" && v !== false && v != null).length
    : 0;

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <LaunchBar
        summary={
          criteria > 0
            ? `A search of the open bids on ${criteria} criteri${criteria === 1 ? "on" : "a"}: every matching bid's detail page, header information and line items — one spreadsheet, no document downloads.`
            : "Every open bid: summary row, detail-page header information and line items — one spreadsheet, no document downloads."
        }
      >
        <div className="flex items-center gap-2">
          <MiniButton
            onClick={() => setAdvanced((open) => !open)}
            disabled={isRunning}
            aria-expanded={advanced}
            aria-controls="philadelphia-advanced-search"
          >
            {advanced ? "Search every open bid" : "Advanced search"}
          </MiniButton>
          <StopButton run={run} onError={setError} />
          <LiveMonitor run={run} portal="philadelphia" />
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
            {criteria > 0 ? "Run search" : "Start scrape"}
          </StartButton>
        </div>
      </LaunchBar>

      {advanced && (
        <div id="philadelphia-advanced-search">
          <PhiladelphiaSearch
            filters={filters}
            onChange={setFilters}
            disabled={starting || isRunning}
          />
        </div>
      )}

      {run && <RunStatusPanel run={run} />}
      {/* The run's log, streaming into the panel that launched it. */}
      <SessionActivity portal="philadelphia" />
      {run && <PhiladelphiaResults bids={run.bids} />}
    </div>
  );
}
