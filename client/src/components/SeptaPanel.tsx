"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import RunStatusPanel from "@/components/RunStatus";
import SeptaResults from "@/components/SeptaResults";
import { Card, ErrorBanner, Field, LaunchBar, StartButton } from "@/components/ui";
import { getRunStatus, startSeptaScrape, type RunStatus } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

export default function SeptaPanel() {
  const [dateFilter, setDateFilter] = useState("");
  const [keyword, setKeyword] = useState("");
  const [commodityCode, setCommodityCode] = useState("");
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
      const { run_id } = await startSeptaScrape({
        dateFilter: dateFilter.trim(),
        keyword: keyword.trim(),
        commodityCode: commodityCode.trim(),
      });
      const status = await getRunStatus("septa", run_id);
      setRun(status);
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const latest = await getRunStatus("septa", run_id);
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
  const hasCriteria = [dateFilter, keyword, commodityCode].some((v) => v.trim() !== "");

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="Search criteria"
        description="All three filters are optional and can be combined — use any one on its own, mix them, or leave them all blank to capture today's open quotes (the portal's default)."
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-xs font-semibold text-ink-700">Opens on date</label>
            <input
              type="date"
              value={dateFilter}
              disabled={isRunning}
              onChange={(e) => setDateFilter(e.target.value)}
              className="w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm transition placeholder:text-ink-400 focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/25 disabled:cursor-not-allowed disabled:bg-ink-50 disabled:text-ink-400"
            />
            <p className="mt-1.5 text-xs text-ink-500">Optional. Format: YYYY-MM-DD.</p>
          </div>
          <Field
            label="Keyword search"
            value={keyword}
            onChange={setKeyword}
            disabled={isRunning}
            placeholder="Enter a keyword"
            hint="Optional. Free-text search of the Open Quotes."
          />
          <Field
            label="Commodity code"
            value={commodityCode}
            onChange={setCommodityCode}
            disabled={isRunning}
            placeholder="xxxx"
            hint="Optional. Filter by SEPTA commodity code."
          />
        </div>
      </Card>

      <LaunchBar
        summary={
          hasCriteria
            ? "Searching Open Quotes with your selected filters."
            : "No filters set — today's open quotes will be captured."
        }
      >
        <StartButton onClick={handleStart} disabled={starting || isRunning} running={isRunning} starting={starting}>
          Search &amp; scrape
        </StartButton>
      </LaunchBar>

      {run && <RunStatusPanel run={run} />}
      {run && <SeptaResults bids={run.bids} />}
    </div>
  );
}
