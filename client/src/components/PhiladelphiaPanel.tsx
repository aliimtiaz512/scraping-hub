"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import PhiladelphiaResults from "@/components/PhiladelphiaResults";
import PhiladelphiaSearch from "@/components/PhiladelphiaSearch";
import RunStatusPanel from "@/components/RunStatus";
import { ErrorBanner, LaunchBar, MiniButton, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import {
  getRunStatus,
  isTerminalStatus,
  startPhiladelphiaScrape,
  type PhiladelphiaFilters,
  type RunStatus,
} from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

/**
 * PHLContracts: the whole Open Bids list, or a search of it.
 *
 * A run defaults to every open bid, which is the portal's full published scope
 * and needs nothing configured. Advanced Search is opt-in and hidden until
 * asked for, because a form that is always on the screen implies a run needs it
 * — and this one does not.
 */
export default function PhiladelphiaPanel() {
  const [run, setRun] = useState<RunStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [filters, setFilters] = useState<PhiladelphiaFilters>({});
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const handleStart = async (livePreview = false) => {
    setError(null);
    setStarting(true);
    try {
      // Criteria only count while the panel is open: closing it is how you go
      // back to the whole list, and a filter still applying after it has been
      // put away would be a run doing something the screen does not show.
      const { run_id } = await startPhiladelphiaScrape(
        livePreview,
        advanced ? filters : {},
      );
      setRun(await getRunStatus("philadelphia", run_id));
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const latest = await getRunStatus("philadelphia", run_id);
          setRun(latest);
          if (isTerminalStatus(latest.status)) stopPolling();
        } catch {
          // transient poll failure — keep trying
        }
      }, POLL_INTERVAL_MS);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  };

  const isRunning = run !== null && (run.status === "pending" || run.status === "running");
  const criteria = advanced
    ? Object.entries(filters).filter(([, v]) => v !== "" && v !== false && v != null).length
    : 0;

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <LaunchBar
        summary={
          criteria > 0
            ? `A search of the open bids on ${criteria} criteri${criteria === 1 ? "on" : "a"}: every matching bid's detail page, header information and attachments — one folder per bid inside a single ZIP.`
            : "Every open bid: summary row, detail-page header information, and all attachments — one folder per bid inside a single ZIP."
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
      {run && <PhiladelphiaResults bids={run.bids} />}
    </div>
  );
}
