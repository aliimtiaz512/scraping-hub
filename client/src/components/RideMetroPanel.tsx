"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import RideMetroResults from "@/components/RideMetroResults";
import RunStatusPanel from "@/components/RunStatus";
import { ErrorBanner, LaunchBar, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import { getRunStatus, startRideMetroScrape, type RunStatus } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

export default function RideMetroPanel() {
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
      const { run_id } = await startRideMetroScrape(livePreview);
      const status = await getRunStatus("ridemetro", run_id);
      setRun(status);
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const latest = await getRunStatus("ridemetro", run_id);
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

      <LaunchBar summary="No configuration needed — this run captures every open public opportunity.">
        <div className="flex items-center gap-2">
          <LiveMonitor run={run} portal="ridemetro" />
          <StartButton onClick={() => handleStart()} disabled={starting || isRunning} running={isRunning} starting={starting}>
            Start scrape
          </StartButton>
        </div>
      </LaunchBar>

      {run && <RunStatusPanel run={run} />}
      {run && <RideMetroResults bids={run.bids} />}
    </div>
  );
}
