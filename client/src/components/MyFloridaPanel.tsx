"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import CategorySelect from "@/components/CategorySelect";
import ResultsTable from "@/components/ResultsTable";
import RunStatusPanel from "@/components/RunStatus";
import { ErrorBanner, StartButton } from "@/components/ui";
import {
  getCategories,
  getRunStatus,
  startMyFloridaScrape,
  type AdStatus,
  type AdType,
  type Category,
  type RunStatus,
  type SearchMode,
} from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

export default function MyFloridaPanel() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [selected, setSelected] = useState("");
  const [mode, setMode] = useState<SearchMode>("keywords");
  const [selectedCodes, setSelectedCodes] = useState<string[]>([]);
  const [selectedKeywords, setSelectedKeywords] = useState<string[]>([]);
  const [adStatuses, setAdStatuses] = useState<AdStatus[]>([]);
  const [adTypes, setAdTypes] = useState<AdType[]>([]);
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

  const current = useMemo(() => categories.find((c) => c.key === selected), [categories, selected]);

  // Switching niche starts over with everything in it selected — the user narrows
  // down from the full list rather than building one up.
  useEffect(() => {
    setSelectedCodes(current?.codes.map((c) => c.code) ?? []);
    setSelectedKeywords(current?.keywords ?? []);
  }, [current]);

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
      const { run_id } = await startMyFloridaScrape({
        category: selected,
        mode,
        codes: selectedCodes,
        keywords: selectedKeywords,
        adStatuses,
        adTypes,
      });
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
  // Nothing checked in the active mode means there is nothing to search for.
  const nothingSelected = mode === "keywords" ? selectedKeywords.length === 0 : selectedCodes.length === 0;

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <CategorySelect
        categories={categories}
        selected={selected}
        mode={mode}
        selectedCodes={selectedCodes}
        selectedKeywords={selectedKeywords}
        adStatuses={adStatuses}
        adTypes={adTypes}
        disabled={isRunning}
        onSelect={setSelected}
        onModeChange={setMode}
        onCodesChange={setSelectedCodes}
        onKeywordsChange={setSelectedKeywords}
        onAdStatusChange={setAdStatuses}
        onAdTypeChange={setAdTypes}
      />

      <StartButton
        onClick={handleStart}
        disabled={!selected || nothingSelected || starting || isRunning}
        running={isRunning}
        starting={starting}
      >
        {mode === "keywords"
          ? `Start keyword scrape (${selectedKeywords.length} ${selectedKeywords.length === 1 ? "search" : "searches"})`
          : "Start commodity code scrape"}
      </StartButton>

      {run && <RunStatusPanel run={run} />}
      {run && <ResultsTable bids={run.bids} />}
    </div>
  );
}
