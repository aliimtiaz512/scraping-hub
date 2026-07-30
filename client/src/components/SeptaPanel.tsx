"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import RunStatusPanel from "@/components/RunStatus";
import SeptaResults from "@/components/SeptaResults";
import { Card, ErrorBanner, LaunchBar, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import { getRunStatus, getSeptaNiches, startSeptaScrape, type RunStatus, type SeptaNiche } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

export default function SeptaPanel() {
  const [niches, setNiches] = useState<SeptaNiche[]>([]);
  const [selectedNiche, setSelectedNiche] = useState("");
  const [dateFilter, setDateFilter] = useState("");
  const [run, setRun] = useState<RunStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    getSeptaNiches()
      .then((data) => setNiches(data.niches))
      .catch((e: Error) => setError(`Could not load niches — is the API running? (${e.message})`));
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const handleStart = async (livePreview = false) => {
    if (!selectedNiche) {
      setError("Select a niche to search.");
      return;
    }
    setError(null);
    setStarting(true);
    try {
      const { run_id } = await startSeptaScrape({
        niche: selectedNiche,
        dateFilter: dateFilter.trim(),
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
  const niche = niches.find((n) => n.key === selectedNiche) ?? null;
  const searchCount = niche ? niche.keyword_count + niche.code_count : 0;
  const inputClass =
    "w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm transition placeholder:text-ink-400 focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/25 disabled:cursor-not-allowed disabled:bg-ink-50 disabled:text-ink-400";

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="Search criteria"
        description="Pick a niche — the scraper searches every keyword and commodity code it owns, one search each, and merges the results into a single deduplicated Excel report."
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-xs font-semibold text-ink-700">Niche</label>
            <select
              value={selectedNiche}
              disabled={isRunning || niches.length === 0}
              onChange={(e) => setSelectedNiche(e.target.value)}
              className={inputClass}
            >
              {niches.length === 0 ? (
                <option value="">No niches configured</option>
              ) : (
                <>
                  <option value="">Select a niche…</option>
                  {niches.map((n) => (
                    <option key={n.key} value={n.key}>
                      {n.label}
                    </option>
                  ))}
                </>
              )}
            </select>
            <p className="mt-1.5 text-xs text-ink-500">
              {niches.length === 0
                ? "Add niches to server/app/scrapers/septa/niches.py and restart the API."
                : niche
                  ? `${niche.keyword_count} keyword${niche.keyword_count === 1 ? "" : "s"}, ${niche.code_count} commodity code${niche.code_count === 1 ? "" : "s"} → ${searchCount} search${searchCount === 1 ? "" : "es"}.`
                  : "Its keywords and commodity codes load automatically."}
            </p>
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-semibold text-ink-700">Opens on date</label>
            <input
              type="date"
              value={dateFilter}
              disabled={isRunning}
              onChange={(e) => setDateFilter(e.target.value)}
              className={inputClass}
            />
            <p className="mt-1.5 text-xs text-ink-500">Optional. Narrows every search to this open date.</p>
          </div>
        </div>
      </Card>

      <LaunchBar
        summary={
          niche
            ? `${niche.label} — ${searchCount} search${searchCount === 1 ? "" : "es"}, merged into one Excel report.`
            : "Select a niche to begin."
        }
      >
        <div className="flex items-center gap-2">
          <StopButton run={run} onError={setError} />
          <LiveMonitor run={run} portal="septa" />
          <StartButton
            onClick={() => handleStart()}
            disabled={starting || isRunning || !selectedNiche}
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
