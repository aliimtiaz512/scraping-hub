"use client";

import { useEffect, useState } from "react";

import RunStatusPanel from "@/components/RunStatus";
import UnisonResults from "@/components/UnisonResults";
import { Card, ErrorBanner, LaunchBar, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import RefreshButton from "@/components/RefreshButton";
import ScraperControls from "@/components/ScraperControls";
import SessionActivity from "@/components/SessionActivity";
import { usePortalSession } from "@/lib/usePortalSession";
import {
  isRunActive,
  getUnisonFilters,
  startUnisonScrape,
  type UnisonFilter,
} from "@/lib/api";

const NO_FILTER = "-1";

/**
 * Unison: pick the portal's own Filter By criterion, then run.
 *
 * The options are the ones on the seller dashboard's dropdown, served by the
 * backend so the values the scraper selects and the labels shown here cannot
 * drift apart. "Select Criteria" is the portal's own default and means no
 * filter — every open buy. Each buy's detail page is then opened, its documents
 * downloaded, and the bid evaluated against the company criteria.
 */
export default function UnisonPanel() {
  const [filters, setFilters] = useState<UnisonFilter[]>([]);
  const [filterId, setFilterId] = useState<string>(NO_FILTER);
  // The run, its log tail, its error banner and the launching flag all live in
  // the global session registry — not here. That is what lets this panel be
  // unmounted and remounted (or simply hidden) without the scrape noticing.
  const { run, error, starting, setError, launch, applyStatus, hasSession, reset } =
    usePortalSession("unison");
  useEffect(() => {
    let cancelled = false;
    getUnisonFilters()
      .then(({ filters: fetched, default: fallback }) => {
        if (cancelled) return;
        setFilters(fetched);
        setFilterId(fallback);
      })
      .catch((e) => !cancelled && setError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, []);

  const handleStart = (livePreview = false) => launch(() => startUnisonScrape(filterId, livePreview));

  // Paused is still running as far as this panel is concerned: the form stays
  // locked and Start stays disabled until the run really ends.
  const isRunning = isRunActive(run);
  const selected = filters.find((f) => f.value === filterId);

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="Filter by"
        description="The seller dashboard's own criteria. Leave it on Select Criteria to sweep every open buy; the run reads 100 per page and walks every page either way."
      >
        <select
          value={filterId}
          disabled={isRunning || filters.length === 0}
          onChange={(e) => setFilterId(e.target.value)}
          aria-label="Filter By - Criteria"
          className="w-full max-w-sm rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm transition focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/30 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {filters.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </Card>

      <LaunchBar
        summary={
          filterId === NO_FILTER
            ? "Every open buy on the dashboard — each one's detail page read, its documents downloaded, and the bid evaluated."
            : `Buys matching “${selected?.label ?? filterId}” — each one's detail page read, its documents downloaded, and the bid evaluated.`
        }
      >
        <div className="flex items-center gap-2">
          <StopButton run={run} onError={setError} />
          <LiveMonitor run={run} portal="unison" />
          <ScraperControls
            run={run}
            onError={setError}
            onStatusChange={applyStatus}
          />
          <RefreshButton run={run} hasSession={hasSession} onReset={reset} />
          <StartButton onClick={() => handleStart()} disabled={starting || isRunning} running={isRunning} starting={starting}>
            Run scraper
          </StartButton>
        </div>
      </LaunchBar>

      {run && <RunStatusPanel run={run} />}
      {/* The run's log, streaming into the panel that launched it. */}
      <SessionActivity portal="unison" />
      {run && <UnisonResults bids={run.bids} />}
    </div>
  );
}
