"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import CategorySelect from "@/components/CategorySelect";
import MyFloridaSweep from "@/components/MyFloridaSweep";
import MyFloridaSweepResults from "@/components/MyFloridaSweepResults";
import ResultsTable from "@/components/ResultsTable";
import RunStatusPanel from "@/components/RunStatus";
import { ErrorBanner, LaunchBar, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import {
  getCategories,
  getRunStatus,
  getSweepRunStatus,
  startMyFloridaScrape,
  startMyFloridaSweep,
  SWEEP_SCRAPER,
  type AdStatus,
  type AdStatusOption,
  type AdType,
  type Category,
  type RunStatus,
  type SearchMode,
} from "@/lib/api";

/**
 * The panel drives two independent flows. "codes" and "keywords" are the niche
 * search, unchanged. "sweep" is the ad-status sweep, which shares the login and
 * search navigation but runs on its own endpoint, its own run key
 * (myflorida_sweep) and its own classifier — see app/scrapers/myflorida/sweep.
 */
type PanelMode = SearchMode | "sweep";

const POLL_INTERVAL_MS = 3000;

export default function MyFloridaPanel() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [selected, setSelected] = useState("");
  const [mode, setMode] = useState<PanelMode>("keywords");
  const [sweepStatuses, setSweepStatuses] = useState<AdStatusOption[]>(["open"]);
  const [sweepMaxBids, setSweepMaxBids] = useState<number | null>(null);
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

  const handleStart = async (livePreview = false) => {
    setError(null);
    setStarting(true);
    // The sweep polls its own endpoint; everything else is the niche flow's.
    const isSweep = mode === "sweep";
    const poll = isSweep
      ? (id: string) => getSweepRunStatus(id)
      : (id: string) => getRunStatus("myflorida", id);
    try {
      const { run_id } = isSweep
        ? await startMyFloridaSweep({
            adStatuses: sweepStatuses,
            maxBids: sweepMaxBids,
            livePreview,
          })
        : await startMyFloridaScrape({
            category: selected,
            mode,
            codes: selectedCodes,
            keywords: selectedKeywords,
            adStatuses,
            adTypes,
            livePreview,
          });
      setRun(await poll(run_id));
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const latest = await poll(run_id);
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
  // Nothing checked in the active mode means there is nothing to search for.
  const nothingSelected =
    mode === "sweep"
      ? sweepStatuses.length === 0
      : mode === "keywords"
        ? selectedKeywords.length === 0
        : selectedCodes.length === 0;

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <ModeTabs mode={mode} disabled={isRunning} onChange={setMode} />

      {mode === "sweep" ? (
        <MyFloridaSweep
          adStatuses={sweepStatuses}
          maxBids={sweepMaxBids}
          disabled={isRunning}
          onStatusChange={setSweepStatuses}
          onMaxBidsChange={setSweepMaxBids}
        />
      ) : (
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
      )}

      <LaunchBar summary={launchSummary(mode, nothingSelected, {
        keywords: selectedKeywords.length,
        codes: selectedCodes.length,
        statuses: sweepStatuses.length,
        maxBids: sweepMaxBids,
      })}>
        <div className="flex items-center gap-2">
          <StopButton run={run} onError={setError} />
          <LiveMonitor run={run} portal="myflorida" />
          <StartButton
            onClick={() => handleStart()}
            disabled={(mode !== "sweep" && !selected) || nothingSelected || starting || isRunning}
            running={isRunning}
            starting={starting}
          >
            {mode === "sweep" ? "Start sweep" : "Start scrape"}
          </StartButton>
        </div>
      </LaunchBar>

      {run && <RunStatusPanel run={run} />}
      {/* Keyed off the run, not the `mode` tab: the results stay on screen when
          the user switches tabs, so the table has to match the run that
          produced the rows rather than whatever is selected now. */}
      {run &&
        (run.scraper === SWEEP_SCRAPER ? (
          <MyFloridaSweepResults bids={run.bids} />
        ) : (
          <ResultsTable bids={run.bids} />
        ))}
    </div>
  );
}

const MODE_TABS: { key: PanelMode; label: string; hint: string }[] = [
  { key: "keywords", label: "Keywords", hint: "One search per niche keyword" },
  { key: "codes", label: "Commodity codes", hint: "One search across the niche's codes" },
  { key: "sweep", label: "Full sweep", hint: "Every ad of a status, classified into niches" },
];

/** Switches between the niche search and the ad-status sweep. */
function ModeTabs({
  mode,
  disabled,
  onChange,
}: {
  mode: PanelMode;
  disabled?: boolean;
  onChange: (next: PanelMode) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {MODE_TABS.map((tab) => (
        <button
          key={tab.key}
          type="button"
          title={tab.hint}
          disabled={disabled}
          onClick={() => onChange(tab.key)}
          aria-pressed={mode === tab.key}
          className={`rounded-lg border px-3.5 py-2 text-sm transition disabled:cursor-not-allowed disabled:opacity-50 ${
            mode === tab.key
              ? "border-gold-400 bg-gold-50 font-medium text-gold-700"
              : "border-ink-200 bg-white text-ink-600 hover:border-ink-300 hover:bg-ink-50 hover:text-ink-900"
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

function launchSummary(
  mode: PanelMode,
  nothingSelected: boolean,
  counts: { keywords: number; codes: number; statuses: number; maxBids: number | null },
): string {
  if (mode === "sweep") {
    if (nothingSelected) return "Pick at least one ad status to run a sweep.";
    const cap = counts.maxBids ? ` · capped at ${counts.maxBids} ads` : "";
    return `${counts.statuses} ${counts.statuses === 1 ? "status" : "statuses"} · every ad classified into a niche${cap}`;
  }
  if (nothingSelected) {
    return `Select at least one ${mode === "keywords" ? "keyword" : "commodity code"} to run a search.`;
  }
  return mode === "keywords"
    ? `${counts.keywords} ${counts.keywords === 1 ? "search" : "searches"} · one per keyword`
    : `${counts.codes} ${counts.codes === 1 ? "code" : "codes"} in a single search`;
}
