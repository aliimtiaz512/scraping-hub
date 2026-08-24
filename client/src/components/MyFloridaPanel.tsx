"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import CategorySelect from "@/components/CategorySelect";
import MyFloridaSweep from "@/components/MyFloridaSweep";
import MyFloridaSweepResults from "@/components/MyFloridaSweepResults";
import ResultsTable from "@/components/ResultsTable";
import RunStatusPanel from "@/components/RunStatus";
import { Card, ErrorBanner, LaunchBar, SegmentedControl, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import {
  getCategories,
  getMyFloridaAccounts,
  getRunStatus,
  getSweepRunStatus,
  startMyFloridaScrape,
  startMyFloridaSweep,
  SWEEP_SCRAPER,
  type AdStatus,
  type AdStatusOption,
  type AdType,
  type Category,
  type MyFloridaAccount,
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
  const [accounts, setAccounts] = useState<MyFloridaAccount[]>([]);
  const [account, setAccount] = useState("");
  const [selected, setSelected] = useState("");
  const [mode, setMode] = useState<PanelMode>("keywords");
  const [sweepStatuses, setSweepStatuses] = useState<AdStatusOption[]>(["open"]);
  const [sweepMaxBids, setSweepMaxBids] = useState<number | null>(null);
  const [selectedCodes, setSelectedCodes] = useState<string[]>([]);
  const [selectedKeywords, setSelectedKeywords] = useState<string[]>([]);
  const [adStatuses, setAdStatuses] = useState<AdStatus[]>([]);
  const [adTypes, setAdTypes] = useState<AdType[]>([]);
  // The posting-date window. Deliberately **outside** the mode branch: it
  // belongs to the search form rather than to what is typed into it, so it
  // applies to keyword runs, commodity-code runs and sweeps alike — and it
  // survives switching between the three, which is what someone comparing the
  // same window across modes would expect.
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
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

  // Which accounts exist and which can run are the server's to say, so the
  // picker is built from its answer — a third login added to .env shows up
  // without a frontend change.
  useEffect(() => {
    getMyFloridaAccounts()
      .then(({ accounts: fetched, default: fallback }) => {
        setAccounts(fetched);
        // Land on a usable account: the server's default if it can run, else
        // the first that can, else the default so the picker still shows why.
        const usable = fetched.find((a) => a.key === fallback && a.configured)
          ?? fetched.find((a) => a.configured);
        setAccount(usable?.key ?? fallback);
      })
      .catch((e: Error) => setError(`Could not load accounts — is the API running? (${e.message})`));
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
            account,
            startDate: startDate || null,
            endDate: endDate || null,
            livePreview,
          })
        : await startMyFloridaScrape({
            category: selected,
            mode,
            codes: selectedCodes,
            keywords: selectedKeywords,
            adStatuses,
            adTypes,
            account,
            startDate: startDate || null,
            endDate: endDate || null,
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
  const activeAccount = accounts.find((a) => a.key === account);
  // Only once the catalog has loaded: before that there is nothing to be wrong
  // about, and a warning that flashes on every mount is noise.
  const accountBlocked = accounts.length > 0 && !activeAccount?.configured;
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

      <Card
        title="Account"
        description="Which client's vendor registration to run as. Both see the same catalogue of advertisements — this decides whose account does the searching, and which inbox the one-time password arrives in."
      >
        <SegmentedControl
          name="myflorida-account"
          value={account}
          options={accounts.map((option) => ({
            value: option.key,
            label: option.label,
            hint: option.configured
              ? "Credentials configured"
              : `Not configured — set ${option.username_env} and ${option.password_env} in server/.env`,
          }))}
          onChange={setAccount}
          disabled={isRunning}
        />
        {accountBlocked && (
          <p className="mt-3 text-xs leading-relaxed text-red-700">
            {activeAccount?.label ?? "This account"} has no credentials on the server, so a run
            cannot sign in. Add {activeAccount?.username_env} and {activeAccount?.password_env} to{" "}
            <code className="font-mono">server/.env</code> and restart the API.
          </p>
        )}
      </Card>

      <PostingDateWindow
        start={startDate}
        end={endDate}
        disabled={isRunning}
        onStartChange={setStartDate}
        onEndChange={setEndDate}
      />

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
        account: accountBlocked ? null : activeAccount?.label ?? null,
        window: describeWindow(startDate, endDate),
      })}>
        <div className="flex items-center gap-2">
          <StopButton run={run} onError={setError} />
          <LiveMonitor run={run} portal="myflorida" />
          <StartButton
            onClick={() => handleStart()}
            disabled={
              (mode !== "sweep" && !selected) ||
              nothingSelected ||
              starting ||
              isRunning ||
              accountBlocked ||
              !account
            }
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
  counts: {
    keywords: number;
    codes: number;
    statuses: number;
    maxBids: number | null;
    account: string | null;
    window: string;
  },
): string {
  // Which account a run signs in as is not a detail — it is whose bids come
  // back. So it leads the line, next to the button, rather than being something
  // you have to scroll up to check.
  if (!counts.account) return "Choose a configured account to run.";
  const as = `Runs as ${counts.account}`;
  // Appended to every mode's line rather than written into each: the window
  // applies to all three, and a summary that mentioned it in some modes and not
  // others would read as though it did not.
  const when = counts.window ? ` · ${counts.window}` : "";
  if (mode === "sweep") {
    if (nothingSelected) return `${as} · pick at least one ad status to run a sweep.`;
    const cap = counts.maxBids ? ` · capped at ${counts.maxBids} ads` : "";
    return `${as} · ${counts.statuses} ${counts.statuses === 1 ? "status" : "statuses"} · every ad classified into a niche${cap}${when}`;
  }
  if (nothingSelected) {
    return `${as} · select at least one ${mode === "keywords" ? "keyword" : "commodity code"} to run a search.`;
  }
  return mode === "keywords"
    ? `${as} · ${counts.keywords} ${counts.keywords === 1 ? "search" : "searches"} · one per keyword${when}`
    : `${as} · ${counts.codes} ${counts.codes === 1 ? "code" : "codes"} in a single search${when}`;
}

/** The window in words, for the launch line. Empty when nothing is set, so the
 *  summary reads exactly as it did before this existed. */
function describeWindow(start: string, end: string): string {
  if (start && end) return `posted ${start} to ${end}`;
  if (start) return `posted on or after ${start}`;
  if (end) return `posted on or before ${end}`;
  return "";
}

/** Start and end of the posting-date window, shared by all three modes.
 *
 *  Placed above the mode-specific panel rather than inside it, because it is
 *  the one control here that means the same thing whichever mode is selected —
 *  putting a copy in each would invite them to drift apart. */
function PostingDateWindow({
  start,
  end,
  disabled,
  onStartChange,
  onEndChange,
}: {
  start: string;
  end: string;
  disabled?: boolean;
  onStartChange: (value: string) => void;
  onEndChange: (value: string) => void;
}) {
  // Either end may stand alone — "everything since the first of the month" is a
  // normal thing to ask for, and demanding a closing date for it would only
  // invite someone to type today's and get a window that stops being right
  // tomorrow. An inverted range is the one combination the server rejects, so
  // it is flagged here before the button is pressed.
  const inverted = Boolean(start && end && end < start);

  return (
    <Card
      title="Posting date"
      description="Narrows every mode to advertisements posted inside this window. Leave both blank for every posting date; fill only one end for an open-ended window."
    >
      <div className="flex flex-wrap items-end gap-4">
        <DateField label="Start date" value={start} disabled={disabled} onChange={onStartChange} />
        <DateField label="End date" value={end} disabled={disabled} onChange={onEndChange} />
        {(start || end) && !disabled && (
          <button
            type="button"
            onClick={() => {
              onStartChange("");
              onEndChange("");
            }}
            className="pb-1.5 text-xs font-medium text-ink-500 underline-offset-2 hover:text-ink-800 hover:underline"
          >
            Clear
          </button>
        )}
      </div>
      {inverted && (
        <p className="mt-3 text-xs leading-relaxed text-red-700">
          The end date is before the start date — no advertisement can fall in that window.
        </p>
      )}
    </Card>
  );
}

/** A native date input. The console speaks ISO throughout; the server converts
 *  to the mm/dd/yyyy the portal's own fields take. */
function DateField({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <div>
      <label className="mb-1.5 block text-xs font-semibold text-ink-700">{label}</label>
      <input
        type="date"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="rounded-lg border border-ink-200 bg-white px-3 py-1.5 text-sm text-ink-900 shadow-sm focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/25 disabled:cursor-not-allowed disabled:bg-ink-50"
      />
    </div>
  );
}
