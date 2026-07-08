"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import CategorySelect from "@/components/CategorySelect";
import ResultsTable from "@/components/ResultsTable";
import RunStatusPanel from "@/components/RunStatus";
import { getCategories, getRunStatus, startMyFloridaScrape, type Category, type RunStatus } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

export default function MyFloridaPanel() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [selected, setSelected] = useState("");
  const [priority, setPriority] = useState("high");
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
      const { run_id } = await startMyFloridaScrape(selected, priority);
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
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      <CategorySelect
        categories={categories}
        selected={selected}
        priority={priority}
        disabled={isRunning}
        onSelect={setSelected}
        onPriorityChange={setPriority}
      />

      <button
        type="button"
        onClick={handleStart}
        disabled={!selected || starting || isRunning}
        className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {isRunning ? "Run in progress…" : starting ? "Starting…" : "Start scrape"}
      </button>

      {run && <RunStatusPanel run={run} />}
      {run && <ResultsTable bids={run.bids} />}
    </div>
  );
}
