"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import RunStatusPanel from "@/components/RunStatus";
import UnisonResults from "@/components/UnisonResults";
import { Card, ErrorBanner, Field, LaunchBar, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import { getRunStatus, startUnisonScrape, type RunStatus } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

export default function UnisonPanel() {
  const [filterBy, setFilterBy] = useState("");
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
      const { run_id } = await startUnisonScrape(filterBy.trim(), livePreview);
      const status = await getRunStatus("unison", run_id);
      setRun(status);
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const latest = await getRunStatus("unison", run_id);
          setRun(latest);
          if (latest.status === "completed" || latest.status === "failed") stopPolling();
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

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="Scrape options"
        description="Leave the filter blank to capture every open buyer request, or narrow the dashboard with an optional filter term."
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Filter (optional)"
            value={filterBy}
            onChange={setFilterBy}
            disabled={isRunning}
            placeholder="e.g. a keyword or category"
          />
        </div>
      </Card>

      <LaunchBar
        summary={
          filterBy.trim()
            ? `Filtering requests by “${filterBy.trim()}”.`
            : "No filter set — every open buyer request will be captured."
        }
      >
        <div className="flex items-center gap-2">
          <LiveMonitor run={run} portal="unison" />
          <StartButton onClick={() => handleStart()} disabled={starting || isRunning} running={isRunning} starting={starting}>
            Start scrape
          </StartButton>
        </div>
      </LaunchBar>

      {run && <RunStatusPanel run={run} />}
      {run && <UnisonResults bids={run.bids} />}
    </div>
  );
}
