"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import BidnetFilters from "@/components/BidnetFilters";
import BidnetNicheSelect from "@/components/BidnetNicheSelect";
import BidnetResults from "@/components/BidnetResults";
import RunStatusPanel, { stepLabel } from "@/components/RunStatus";
import { ErrorBanner, LaunchBar, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import {
  getBidnetFilters,
  getBidnetNiches,
  getRunStatus,
  refreshBidnetFilterOptions,
  startBidnetScrape,
  type BidnetFilterCatalog,
  type BidnetFilters as Filters,
  type BidnetNiche,
  type RunStatus,
} from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

export default function BidnetPanel() {
  const [catalog, setCatalog] = useState<BidnetFilterCatalog | null>(null);
  const [niches, setNiches] = useState<BidnetNiche[]>([]);
  const [selectedNiche, setSelectedNiche] = useState("");
  const [filters, setFilters] = useState<Filters>({});
  const [run, setRun] = useState<RunStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  // The option-discovery pass is its own run, tracked apart from the scrape: it
  // produces no bids and nothing to download, so it must not land in `run` and
  // be rendered as a (broken) scrape result.
  const [refreshRun, setRefreshRun] = useState<RunStatus | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const refreshPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadCatalog = useCallback(
    () =>
      getBidnetFilters()
        .then((data) => {
          setCatalog(data);
          setFilters((current) => ({ status: data.status.default, ...current }));
        })
        .catch((e: Error) =>
          setError(`Could not load BidNet filters — is the API running? (${e.message})`),
        ),
    [],
  );

  useEffect(() => {
    void loadCatalog();
    getBidnetNiches()
      .then((data) => setNiches(data.niches))
      .catch((e: Error) =>
        setError(`Could not load niches — is the API running? (${e.message})`),
      );
  }, [loadCatalog]);

  const stopPolling = useCallback(() => {
    for (const ref of [pollRef, refreshPollRef]) {
      if (ref.current) {
        clearInterval(ref.current);
        ref.current = null;
      }
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  /** Poll one run to completion into `setState`, then run `onFinish`. */
  const poll = useCallback(
    (
      ref: typeof pollRef,
      runId: string,
      setState: (run: RunStatus) => void,
      onFinish?: () => void,
    ) => {
      if (ref.current) clearInterval(ref.current);
      ref.current = setInterval(async () => {
        try {
          const latest = await getRunStatus("bidnet", runId);
          setState(latest);
          if (latest.status === "completed" || latest.status === "failed" || latest.status === "stopped") {
            if (ref.current) clearInterval(ref.current);
            ref.current = null;
            onFinish?.();
          }
        } catch {
          // transient poll failure — keep trying
        }
      }, POLL_INTERVAL_MS);
    },
    [],
  );

  const handleStart = async (livePreview = false) => {
    setError(null);
    setStarting(true);
    try {
      const { run_id } = await startBidnetScrape(selectedNiche, filters, livePreview);
      setRun(await getRunStatus("bidnet", run_id));
      poll(pollRef, run_id, setRun);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  };

  /** Harvest BidNet's full option lists, then reload the catalog with them. */
  const handleRefreshOptions = async () => {
    setError(null);
    try {
      const { run_id } = await refreshBidnetFilterOptions();
      setRefreshRun(await getRunStatus("bidnet", run_id));
      poll(refreshPollRef, run_id, setRefreshRun, () => void loadCatalog());
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const isRunning = run !== null && (run.status === "pending" || run.status === "running");
  const refreshing = refreshRun !== null && (refreshRun.status === "pending" || refreshRun.status === "running");
  // Purchasing Group is the one filter that can be emptied into a search that
  // matches nothing — BidNet treats "no group" as "no results".
  const noPurchasingGroup = filters.purchasing_groups?.length === 0;
  const niche = niches.find((n) => n.key === selectedNiche) ?? null;
  const blocked =
    (niches.length === 0 &&
      "No niches configured — add them to server/app/scrapers/bidnet/niches.py and restart the API.") ||
    (!selectedNiche && "Select a niche to search.") ||
    (noPurchasingGroup &&
      "Select at least one purchasing group — BidNet returns nothing without one.") ||
    null;

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      {refreshRun && <RefreshNotice run={refreshRun} />}

      <BidnetNicheSelect
        niches={niches}
        selected={selectedNiche}
        disabled={isRunning}
        onSelect={setSelectedNiche}
      />

      {catalog && (
        <BidnetFilters
          catalog={catalog}
          filters={filters}
          disabled={isRunning}
          refreshing={refreshing}
          onChange={setFilters}
          onRefreshOptions={handleRefreshOptions}
        />
      )}

      <LaunchBar summary={blocked ?? launchSummary(niche, filters, catalog)}>
        <div className="flex items-center gap-2">
          <StopButton run={run} onError={setError} />
          <LiveMonitor run={run} portal="bidnet" />
          <StartButton
            onClick={() => handleStart()}
            disabled={starting || isRunning || blocked !== null}
            running={isRunning}
            starting={starting}
          >
            Start scrape
          </StartButton>
        </div>
      </LaunchBar>

      {run && <RunStatusPanel run={run} />}
      {run && <BidnetResults bids={run.bids} />}
    </div>
  );
}

/** Progress line for the option-discovery pass — it logs into BidNet and drives
 *  a browser, so it takes long enough to need saying what it is doing. */
function RefreshNotice({ run }: { run: RunStatus }) {
  const done = run.status === "completed";
  const failed = run.status === "failed" || run.status === "stopped";
  const counts = run.filter_option_counts
    ? Object.values(run.filter_option_counts).reduce((sum, n) => sum + n, 0)
    : null;

  return (
    <div
      className={`rounded-xl border px-5 py-3 text-sm ${
        failed
          ? "border-red-200 bg-red-50 text-red-700"
          : done
            ? "border-ink-200 bg-white text-ink-600"
            : "border-gold-300 bg-gold-50/70 text-gold-800"
      }`}
    >
      {failed
        ? `Reading BidNet's filter options failed${run.errors?.[0] ? ` — ${run.errors[0]}` : "."}`
        : done
          ? `Filter options refreshed from BidNet${counts !== null ? ` — ${counts} options across all panels.` : "."}`
          : `Reading BidNet's filter options — ${stepLabel(run.step)}…`}
    </div>
  );
}

function launchSummary(
  niche: BidnetNiche | null,
  filters: Filters,
  catalog: BidnetFilterCatalog | null,
): string {
  const status = catalog?.status.options.find(
    (option) => option.value === (filters.status ?? catalog.status.default),
  );
  const active = (catalog?.sections ?? [])
    .filter((section) => {
      const chosen = filters[section.name];
      if (!chosen) return false;
      // A full purchasing-group selection is the portal's default, not a filter.
      return section.default_all ? chosen.length < section.options.length : chosen.length > 0;
    })
    .map((section) => section.label);
  for (const name of ["published_date", "closing_date"] as const) {
    if (filters[name]) active.push(name === "published_date" ? "Published Date" : "Closing Date");
  }

  const count = niche?.keyword_count ?? 0;
  const base =
    `${count} ${count === 1 ? "search" : "searches"}, one per keyword · ` +
    `${status?.label ?? "Open Solicitations"}`;
  return active.length === 0 ? `${base} · no other filters.` : `${base} · ${active.join(", ")}.`;
}
