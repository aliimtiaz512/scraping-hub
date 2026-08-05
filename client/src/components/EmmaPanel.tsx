"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import EmmaResults from "@/components/EmmaResults";
import RunStatusPanel from "@/components/RunStatus";
import { Card, ErrorBanner, Field, LaunchBar, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import { getRunStatus, startEmmaScrape, type RunStatus } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

export default function EmmaPanel() {
  const [keyword, setKeyword] = useState("");
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
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
      const { run_id } = await startEmmaScrape({
        keyword: keyword.trim(),
        status: status.trim(),
        category: category.trim(),
        livePreview,
      });
      setRun(await getRunStatus("emma", run_id));
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const latest = await getRunStatus("emma", run_id);
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
  const hasCriteria = [keyword, status, category].some((v) => v.trim() !== "");

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="Filters"
        description="The same three filters the portal shows above Public Solicitations — all optional and combinable. Leave them blank to capture every public solicitation. Each opened bid has all its fields extracted and its documents downloaded; only solicitations closing at least 7 days out are kept."
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <Field
            label="Keywords"
            value={keyword}
            onChange={setKeyword}
            disabled={isRunning}
            placeholder="e.g. engineering"
          />
          <Field
            label="Status"
            value={status}
            onChange={setStatus}
            disabled={isRunning}
            placeholder="e.g. Open"
          />
          <Field
            label="Category"
            value={category}
            onChange={setCategory}
            disabled={isRunning}
            placeholder="e.g. Civil engineering"
          />
        </div>
      </Card>

      <LaunchBar
        summary={
          hasCriteria
            ? "Searching Public Solicitations with your filters."
            : "No filters set — every public solicitation will be captured."
        }
      >
        <div className="flex items-center gap-2">
          <StopButton run={run} onError={setError} />
          <LiveMonitor run={run} portal="emma" />
          <StartButton onClick={() => handleStart()} disabled={starting || isRunning} running={isRunning} starting={starting}>
            Search &amp; scrape
          </StartButton>
        </div>
      </LaunchBar>

      {run && <RunStatusPanel run={run} />}
      {run && <EmmaResults bids={run.bids} />}
    </div>
  );
}
