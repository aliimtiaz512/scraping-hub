"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import BidnetResults from "@/components/BidnetResults";
import KeywordSelect from "@/components/KeywordSelect";
import RunStatusPanel from "@/components/RunStatus";
import { ErrorBanner, LaunchBar, StartButton } from "@/components/ui";
import { getBidnetKeywords, getRunStatus, startBidnetScrape, type BidnetNiche, type RunStatus } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

/** How many niche+tier groups (plus a Custom group) the selection spans — this
 * is how many folders/Excels the run will produce. */
function countGroups(niches: BidnetNiche[], selected: string[]): number {
  const chosen = new Set(selected);
  let groups = 0;
  const catalogTerms = new Set<string>();
  for (const niche of niches) {
    for (const list of [niche.core, niche.extended]) {
      const terms = list.map((k) => k.term);
      terms.forEach((t) => catalogTerms.add(t));
      if (terms.some((t) => chosen.has(t))) groups += 1;
    }
  }
  if (selected.some((t) => !catalogTerms.has(t))) groups += 1; // Custom group
  return groups;
}

export default function BidnetPanel() {
  const [niches, setNiches] = useState<BidnetNiche[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [run, setRun] = useState<RunStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    getBidnetKeywords()
      .then((data) => setNiches(data.niches))
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
  const groupCount = countGroups(niches, selected);

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <KeywordSelect niches={niches} selected={selected} disabled={isRunning} onChange={setSelected} />

      <LaunchBar
        summary={
          selected.length === 0
            ? "Select at least one keyword to run a search."
            : `${selected.length} ${selected.length === 1 ? "search" : "searches"} across ${groupCount} ${groupCount === 1 ? "group" : "groups"} · one search per keyword, foldered by niche + tier`
        }
      >
        <StartButton
          onClick={handleStart}
          disabled={selected.length === 0 || starting || isRunning}
          running={isRunning}
          starting={starting}
        >
          Start scrape
        </StartButton>
      </LaunchBar>

      {run && <RunStatusPanel run={run} />}
      {run && <BidnetResults bids={run.bids} />}
    </div>
  );
}
