"use client";

import { useState } from "react";

import NorthDakotaResults from "@/components/NorthDakotaResults";
import RunStatusPanel from "@/components/RunStatus";
import { Card, ErrorBanner, Field, LaunchBar, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import RefreshButton from "@/components/RefreshButton";
import ScraperControls from "@/components/ScraperControls";
import SessionActivity from "@/components/SessionActivity";
import { usePortalSession } from "@/lib/usePortalSession";
import { isRunActive, startNorthDakotaScrape } from "@/lib/api";

export default function NorthDakotaPanel() {
  const [keyword, setKeyword] = useState("");
  const [commodity, setCommodity] = useState("");
  // The run, its log tail, its error banner and the launching flag all live in
  // the global session registry — not here. That is what lets this panel be
  // unmounted and remounted (or simply hidden) without the scrape noticing.
  const { run, error, starting, setError, launch, applyStatus, hasSession, reset } =
    usePortalSession("northdakota");
  const handleStart = (livePreview = false) =>
    launch(() => startNorthDakotaScrape(keyword.trim(), commodity.trim(), livePreview));

  // Paused is still running as far as this panel is concerned: the form stays
  // locked and Start stays disabled until the run really ends.
  const isRunning = isRunActive(run);
  const hasCriteria = [keyword, commodity].some((v) => v.trim() !== "");

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="Search criteria"
        description="Both fields are optional. Leave them blank to capture every public solicitation request. Commodity filtering is applied best-effort against the portal's autocomplete."
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Keywords"
            value={keyword}
            onChange={setKeyword}
            disabled={isRunning}
            placeholder="e.g. janitorial"
          />
          <Field
            label="Commodity"
            value={commodity}
            onChange={setCommodity}
            disabled={isRunning}
            placeholder="e.g. Laboratory Equipment"
          />
        </div>
      </Card>

      <LaunchBar
        summary={
          hasCriteria
            ? "Searching with your criteria."
            : "No criteria set — every public solicitation request will be captured."
        }
      >
        <div className="flex items-center gap-2">
          <StopButton run={run} onError={setError} />
          <LiveMonitor run={run} portal="northdakota" />
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
      <SessionActivity portal="northdakota" />
      {run && <NorthDakotaResults bids={run.bids} />}
    </div>
  );
}
