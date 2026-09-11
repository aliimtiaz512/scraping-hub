"use client";

import { useState } from "react";

import EmmaResults from "@/components/EmmaResults";
import RunStatusPanel from "@/components/RunStatus";
import { Card, ErrorBanner, Field, LaunchBar, StartButton } from "@/components/ui";
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
        livePreview,
      }),
    );

  // Paused is still running as far as this panel is concerned: the form stays
  // locked and Start stays disabled until the run really ends.
  const isRunning = isRunActive(run);
  const hasCriteria = [keyword, status, category].some((v) => v.trim() !== "");

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="Filters"
        description="The same three filters the portal shows above Public Solicitations — all optional and combinable. Leave them blank to capture every public solicitation. Each opened bid has all its fields extracted and its documents downloaded; only solicitations closing at least 7 days out are kept."
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
      </Card>

      <LaunchBar
        summary={
          hasCriteria
            ? "Searching Public Solicitations with your filters."
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
          <StartButton onClick={() => handleStart()} disabled={starting || isRunning} running={isRunning} starting={starting}>
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
