"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import BidnetResults from "@/components/BidnetResults";
import KeywordSelect from "@/components/KeywordSelect";
import RunStatusPanel from "@/components/RunStatus";
import { ErrorBanner, StartButton } from "@/components/ui";
import { getBidnetKeywords, getRunStatus, startBidnetScrape, type KeywordGroup, type RunStatus } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

export default function BidnetPanel() {
  const [groups, setGroups] = useState<KeywordGroup[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [run, setRun] = useState<RunStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    getBidnetKeywords()
      .then((data) => setGroups(data.groups))
      .catch((e: Error) => setError(`Could not load keywords — is the API running? (${e.message})`));
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const handleStart = async () => {
    if (selected.length === 0) {
      setError("Select at least one keyword to search.");
      return;
    }
    setError(null);
    setStarting(true);
    try {
      const { run_id } = await startBidnetScrape(selected);
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

      <KeywordSelect groups={groups} selected={selected} disabled={isRunning} onChange={setSelected} />

      <StartButton
        onClick={handleStart}
        disabled={selected.length === 0 || starting || isRunning}
        running={isRunning}
        starting={starting}
      >
        Start scrape
      </StartButton>

      {run && <RunStatusPanel run={run} />}
      {run && <BidnetResults bids={run.bids} />}
    </div>
  );
}
