"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import BidnetFilters from "@/components/BidnetFilters";
import BidnetNicheSelect from "@/components/BidnetNicheSelect";
import BidnetResults from "@/components/BidnetResults";
import RunStatusPanel, { stepLabel } from "@/components/RunStatus";
import { Button, ErrorBanner, LaunchBar, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import {
  getBidnetFilters,
  getBidnetNiches,
  getRunStatus,
  refreshBidnetFilterOptions,
  runDownloadUrl,
  startBidnetBatch,
  startBidnetScrape,
  type BidnetFilterCatalog,
  type BidnetFilters as Filters,
  type BidnetNiche,
  type BidnetNicheResult,
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

  /** Every niche in one execution, one after another. The same filters apply to
   *  all of them, and each niche is packaged into its own ZIP — so this is the
   *  one control that ignores the niche dropdown. */
  const handleStartAll = async () => {
    setError(null);
    setStarting(true);
    try {
      const { batch_id } = await startBidnetBatch(undefined, filters);
      setRun(await getRunStatus("bidnet", batch_id));
      poll(pollRef, batch_id, setRun);
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
  // What stops *any* run, batch included — the niche dropdown is not one of
  // them, since running everything needs no selection.
  const blockedForAll =
    (niches.length === 0 &&
      "No niches configured — add them to server/app/scrapers/bidnet/niches.py and restart the API.") ||
    (noPurchasingGroup &&
      "Select at least one purchasing group — BidNet returns nothing without one.") ||
    null;
  const blocked = blockedForAll ?? (!selectedNiche ? "Select a niche to search." : null);
  const totalSearches = niches.reduce((sum, n) => sum + (n.search_count ?? n.keyword_count), 0);

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

      <LaunchBar
        summary={
          blockedForAll ??
          (selectedNiche
            ? launchSummary(niche, filters, catalog)
            : `Select a niche to search, or run all ${niches.length} niches in one go — ` +
              `${totalSearches} searches, one ZIP per niche.`)
        }
      >
        <div className="flex items-center gap-2">
          <StopButton run={run} onError={setError} />
          <LiveMonitor run={run} portal="bidnet" />
          <Button
            variant="secondary"
            size="lg"
            onClick={() => void handleStartAll()}
            disabled={starting || isRunning || blockedForAll !== null}
            title={
              blockedForAll ??
              `Run all ${niches.length} niches one after another — ${totalSearches} searches, ` +
                "each niche in its own browser session and its own ZIP."
            }
          >
            Run all niches
          </Button>
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

      {run?.is_batch && <BatchProgress run={run} />}
      {run && <RunStatusPanel run={run} />}
      {run && !run.is_batch && <BidnetResults bids={run.bids} />}
    </div>
  );
}

/** Where a batch has got to: one row per niche, in the order they run.
 *
 *  A batch's own run record holds no bids — each niche has its own run, its own
 *  spreadsheet and its own ZIP — so this replaces the results table rather than
 *  sitting beside it, and links to each niche's download as it lands. */
function BatchProgress({ run }: { run: RunStatus }) {
  const results = run.niche_results ?? [];
  const total = run.niche_total ?? results.length;
  const done = run.niche_done ?? results.length;
  const running = run.status === "running" || run.status === "pending";

  return (
    <section className="rounded-xl border border-ink-200/70 bg-white shadow-sm shadow-ink-900/[0.03]">
      <header className="flex items-baseline justify-between gap-4 border-b border-ink-100 px-5 py-4">
        <h3 className="font-display text-base text-ink-900">
          All niches — {done} of {total} finished
        </h3>
        {running && run.niche_current && (
          <p className="text-xs text-ink-500">Scraping {run.niche_current}…</p>
        )}
      </header>
      <ul className="divide-y divide-ink-100">
        {(run.niches_requested ?? []).map((label, index) => {
          const result = results[index];
          const active = running && !result && run.niche_current === label;
          return (
            <li key={label} className="flex items-center justify-between gap-4 px-5 py-3 text-sm">
              <span className="min-w-0 truncate text-ink-900">{label}</span>
              <NicheOutcome result={result} active={active} />
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function NicheOutcome({ result, active }: { result?: BidnetNicheResult; active: boolean }) {
  if (!result) {
    return (
      <span className="shrink-0 text-xs text-ink-400">{active ? "Running…" : "Queued"}</span>
    );
  }
  if (result.status !== "completed") {
    return (
      <span className="shrink-0 text-xs text-red-600" title={result.error}>
        {result.status === "stopped" ? "Stopped" : "Failed"}
      </span>
    );
  }
  return (
    <span className="flex shrink-0 items-center gap-3 text-xs text-ink-500">
      <span>{result.bids ?? 0} bids</span>
      {result.zip_name && result.run_id && (
        <a
          href={runDownloadUrl(result.run_id)}
          className="font-medium text-gold-700 underline-offset-2 hover:underline"
        >
          Download
        </a>
      )}
    </span>
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

  const count = niche?.search_count ?? niche?.keyword_count ?? 0;
  const base =
    `${count} ${count === 1 ? "search" : "searches"}, one per term · ` +
    `${status?.label ?? "Open Solicitations"}`;
  return active.length === 0 ? `${base} · no other filters.` : `${base} · ${active.join(", ")}.`;
}
