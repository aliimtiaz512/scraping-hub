"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import NorthDakotaResults from "@/components/NorthDakotaResults";
import RunStatusPanel from "@/components/RunStatus";
import { Card, ErrorBanner, Field, LaunchBar, StartButton } from "@/components/ui";
import { getRunStatus, startNorthDakotaScrape, type RunStatus } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

export default function NorthDakotaPanel() {
  const [keyword, setKeyword] = useState("");
  const [commodity, setCommodity] = useState("");
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

  const handleStart = async () => {
    setError(null);
    setStarting(true);
    try {
      const { run_id } = await startNorthDakotaScrape(keyword.trim(), commodity.trim());
      const status = await getRunStatus("northdakota", run_id);
      setRun(status);
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const latest = await getRunStatus("northdakota", run_id);
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
  const hasCriteria = [keyword, commodity].some((v) => v.trim() !== "");

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="Search criteria"
        description="Both fields are optional. Leave them blank to capture every public solicitation request. Commodity filtering is applied best-effort against the portal's autocomplete."
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Keywords"
            value={keyword}
            onChange={setKeyword}
            disabled={isRunning}
            placeholder="e.g. janitorial"
          />
          <Field
            label="Commodity"
            value={commodity}
            onChange={setCommodity}
            disabled={isRunning}
            placeholder="e.g. Laboratory Equipment"
          />
        </div>
      </Card>

      <LaunchBar
        summary={
          hasCriteria
            ? "Searching with your criteria."
            : "No criteria set — every public solicitation request will be captured."
        }
      >
        <StartButton onClick={handleStart} disabled={starting || isRunning} running={isRunning} starting={starting}>
          Search &amp; scrape
        </StartButton>
      </LaunchBar>

      {run && <RunStatusPanel run={run} />}
      {run && <NorthDakotaResults bids={run.bids} />}
    </div>
  );
}
