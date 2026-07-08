"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import BidnetResults from "@/components/BidnetResults";
import RunStatusPanel from "@/components/RunStatus";
import { getRunStatus, startBidnetScrape, type RunStatus } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

export default function BidnetPanel() {
  const [keyword, setKeyword] = useState("");
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
    const trimmed = keyword.trim();
    if (!trimmed) {
      setError("Enter a keyword to search.");
      return;
    }
    setError(null);
    setStarting(true);
    try {
      const { run_id } = await startBidnetScrape(trimmed);
      const status = await getRunStatus("bidnet", run_id);
      setRun(status);
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const latest = await getRunStatus("bidnet", run_id);
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
      <p className="text-sm text-slate-500">
        Searches BidNet Direct for a keyword, filters to Member Agency Bids, downloads every
        solicitation&apos;s documents, and saves an Excel of the results to the BidNet documents
        folder when the run completes.
      </p>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      <div className="flex flex-wrap items-end gap-3">
        <label className="flex-1">
          <span className="mb-1 block text-xs font-medium text-slate-600">Search keyword</span>
          <input
            type="text"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !starting && !isRunning) handleStart();
            }}
            placeholder="e.g. AI, graphic design, software"
            disabled={isRunning}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-blue-500 focus:ring-1 focus:ring-blue-500 disabled:bg-slate-100"
          />
        </label>

        <button
          type="button"
          onClick={handleStart}
          disabled={starting || isRunning}
          className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isRunning ? "Run in progress…" : starting ? "Starting…" : "Start scrape"}
        </button>
      </div>

      {run && <RunStatusPanel run={run} />}
      {run && <BidnetResults bids={run.bids} />}
    </div>
  );
}
