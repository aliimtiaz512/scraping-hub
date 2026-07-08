"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import CategorySelect from "@/components/CategorySelect";
import ResultsTable from "@/components/ResultsTable";
import RunStatusPanel from "@/components/RunStatus";
import { ErrorBanner, StartButton } from "@/components/ui";
import { getCategories, getRunStatus, startMyFloridaScrape, type AdStatus, type Category, type RunStatus } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

export default function MyFloridaPanel() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [selected, setSelected] = useState("");
  const [priority, setPriority] = useState("high");
  const [adStatuses, setAdStatuses] = useState<AdStatus[]>([]);
  const [run, setRun] = useState<RunStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    getCategories()
      .then((data) => {
        setCategories(data.categories);
        if (data.categories.length > 0) setSelected(data.categories[0].key);
      })
      .catch((e: Error) => setError(`Could not load categories — is the API running? (${e.message})`));
  }, []);

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
      const { run_id } = await startMyFloridaScrape(selected, priority, adStatuses);
      const status = await getRunStatus("myflorida", run_id);
      setRun(status);
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const latest = await getRunStatus("myflorida", run_id);
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

      <CategorySelect
        categories={categories}
        selected={selected}
        priority={priority}
        adStatuses={adStatuses}
        disabled={isRunning}
        onSelect={setSelected}
        onPriorityChange={setPriority}
        onAdStatusChange={setAdStatuses}
      />

      <StartButton onClick={handleStart} disabled={!selected || starting || isRunning} running={isRunning} starting={starting}>
        Start scrape
      </StartButton>

      {run && <RunStatusPanel run={run} />}
      {run && <ResultsTable bids={run.bids} />}
    </div>
  );
}
