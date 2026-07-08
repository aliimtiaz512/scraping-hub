"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import BidnetResults from "@/components/BidnetResults";
import RunStatusPanel from "@/components/RunStatus";
import { ErrorBanner, SectionLabel, StartButton } from "@/components/ui";
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
      <p className="text-sm leading-relaxed text-slate-400">
        Searches BidNet Direct for a keyword, filters to Member Agency Bids, downloads every
        solicitation&apos;s documents, and saves an Excel of the results when the run completes.
      </p>

      {error && <ErrorBanner message={error} />}

      <div>
        <SectionLabel>Search keyword</SectionLabel>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex flex-1 items-center gap-2 rounded-xl border border-white/10 bg-slate-950/50 px-3 py-2.5 transition focus-within:border-emerald-400/40 focus-within:ring-1 focus-within:ring-emerald-400/30">
            <span className="font-mono text-sm text-emerald-500">⌕</span>
            <input
              type="text"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !starting && !isRunning) handleStart();
              }}
              placeholder="e.g. AI, graphic design, software"
              disabled={isRunning}
              className="w-full bg-transparent text-sm text-slate-100 outline-none placeholder:text-slate-600 disabled:opacity-50"
            />
          </div>
          <StartButton onClick={handleStart} disabled={starting || isRunning} running={isRunning} starting={starting}>
            Start scrape
          </StartButton>
        </div>
      </div>

      {run && <RunStatusPanel run={run} />}
      {run && <BidnetResults bids={run.bids} />}
    </div>
  );
}
