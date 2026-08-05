"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import RunStatusPanel from "@/components/RunStatus";
import SeptaResults from "@/components/SeptaResults";
import { Card, ErrorBanner, LaunchBar, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import { getRunStatus, startSeptaScrape, type RunStatus } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

export default function SeptaPanel() {
  const [dateFrom, setDateFrom] = useState("");
  const [run, setRun] = useState<RunStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
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
      const { run_id } = await startSeptaScrape({
        dateFrom: dateFrom.trim(),
        livePreview,
      });
      const status = await getRunStatus("septa", run_id);
      setRun(status);
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const latest = await getRunStatus("septa", run_id);
          setRun(latest);
          if (latest.status === "completed" || latest.status === "failed" || latest.status === "stopped") stopPolling();
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
  const inputClass =
    "w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm transition placeholder:text-ink-400 focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/25 disabled:cursor-not-allowed disabled:bg-ink-50 disabled:text-ink-400";

  const summary = dateFrom
    ? `Open quotes opening from ${dateFrom} onward.`
    : "All open quotes — no date filter.";

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="Opens from"
        description="Optional. Leave blank to fetch every open quote. Quotes whose summary names an out-of-scope manufacturer are skipped automatically."
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
                ? "Quotes opening on or after this date. No upper bound."
                : "No date set: the search runs unfiltered and returns the whole Open Quotes grid."}
            </p>
          </div>
        </div>
      </Card>

      <LaunchBar summary={summary}>
        <div className="flex items-center gap-2">
          <StopButton run={run} onError={setError} />
          <LiveMonitor run={run} portal="septa" />
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
      {run && <SeptaResults bids={run.bids} />}
    </div>
  );
}
