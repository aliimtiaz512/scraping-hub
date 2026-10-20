"use client";

import { useState } from "react";

import EmmaResults from "@/components/EmmaResults";
import RunStatusPanel from "@/components/RunStatus";
import { Card, DateField, ErrorBanner, Field, LaunchBar, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import RefreshButton from "@/components/RefreshButton";
import ScraperControls from "@/components/ScraperControls";
import SessionActivity from "@/components/SessionActivity";
import { usePortalSession } from "@/lib/usePortalSession";
import { isRunActive, startEmmaScrape } from "@/lib/api";

export default function EmmaPanel() {
  const [keyword, setKeyword] = useState("");
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
  // Publish Date range, ISO (yyyy-mm-dd) from the native pickers. Either end may
  // be blank; the server discards every bid published outside the range.
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  // The run, its log tail, its error banner and the launching flag all live in
  // the global session registry — not here. That is what lets this panel be
  // unmounted and remounted (or simply hidden) without the scrape noticing.
  const { run, error, starting, setError, launch, applyStatus, hasSession, reset } =
    usePortalSession("emma");
  const handleStart = (livePreview = false) =>
    launch(() =>
      startEmmaScrape({
        keyword: keyword.trim(),
        status: status.trim(),
        category: category.trim(),
        dateFrom,
        dateTo,
        livePreview,
      }),
    );

  // Paused is still running as far as this panel is concerned: the form stays
  // locked and Start stays disabled until the run really ends.
  const isRunning = isRunActive(run);
  const hasCriteria = [keyword, status, category, dateFrom, dateTo].some((v) => v.trim() !== "");
  // ISO dates compare correctly as strings. The pickers' min/max already stop
  // this, but a typed-in date can still get past them.
  const rangeInverted = dateFrom !== "" && dateTo !== "" && dateTo < dateFrom;
  const rangeText =
    dateFrom && dateTo
      ? `published ${dateFrom} to ${dateTo}`
      : dateFrom
        ? `published on or after ${dateFrom}`
        : dateTo
          ? `published on or before ${dateTo}`
          : "";

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="Filters"
        description="The same three filters the portal shows above Public Solicitations, plus a Publish Date range — all optional and combinable. Leave them blank to capture every public solicitation. Bids published outside the range are discarded; only solicitations closing at least 7 days out are kept."
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <Field
            label="Keywords"
            value={keyword}
            onChange={setKeyword}
            disabled={isRunning}
            placeholder="e.g. engineering"
          />
          <Field
            label="Status"
            value={status}
            onChange={setStatus}
            disabled={isRunning}
            placeholder="e.g. Open"
          />
          <Field
            label="Category"
            value={category}
            onChange={setCategory}
            disabled={isRunning}
            placeholder="e.g. Civil engineering"
          />
        </div>

        <div className="mt-5 border-t border-ink-100 pt-5">
          <p className="mb-3 text-xs font-semibold text-ink-700">Publish Date range</p>
          <div className="flex flex-wrap items-end gap-4">
            <DateField
              label="From"
              value={dateFrom}
              max={dateTo}
              disabled={isRunning}
              onChange={setDateFrom}
            />
            <DateField
              label="To"
              value={dateTo}
              min={dateFrom}
              disabled={isRunning}
              onChange={setDateTo}
            />
          </div>
          {rangeInverted ? (
            <p className="mt-2 text-xs text-rose-600">The To date is before the From date — no bid can be published in that range.</p>
          ) : (
            <p className="mt-2 text-xs text-ink-500">Both days are included. Leave either blank for an open-ended range.</p>
          )}
        </div>
      </Card>

      <LaunchBar
        summary={
          rangeInverted
            ? "Fix the date range before starting."
            : hasCriteria
              ? `Searching Public Solicitations with your filters${rangeText ? ` — only bids ${rangeText}` : ""}.`
              : "No filters set — every public solicitation will be captured."
        }
      >
        <div className="flex items-center gap-2">
          <StopButton run={run} onError={setError} />
          <LiveMonitor run={run} portal="emma" />
          <ScraperControls
            run={run}
            onError={setError}
            onStatusChange={applyStatus}
          />
          <RefreshButton run={run} hasSession={hasSession} onReset={reset} />
          <StartButton onClick={() => handleStart()} disabled={starting || isRunning || rangeInverted} running={isRunning} starting={starting}>
            Search &amp; scrape
          </StartButton>
        </div>
      </LaunchBar>

      {run && <RunStatusPanel run={run} />}
      {/* The run's log, streaming into the panel that launched it. */}
      <SessionActivity portal="emma" />
      {run && <EmmaResults bids={run.bids} />}
    </div>
  );
}
