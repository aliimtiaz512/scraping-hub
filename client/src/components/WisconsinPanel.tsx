"use client";

import { useState } from "react";

import RunStatusPanel from "@/components/RunStatus";
import WisconsinResults from "@/components/WisconsinResults";
import { Card, ErrorBanner, Field, LaunchBar, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import RefreshButton from "@/components/RefreshButton";
import ScraperControls from "@/components/ScraperControls";
import SessionActivity from "@/components/SessionActivity";
import { usePortalSession } from "@/lib/usePortalSession";
import { isRunActive, startWisconsinScrape } from "@/lib/api";

export default function WisconsinPanel() {
  const [keyword, setKeyword] = useState("");
  const [agency, setAgency] = useState("");
  const [nigp, setNigp] = useState("");
  // The run, its log tail, its error banner and the launching flag all live in
  // the global session registry — not here. That is what lets this panel be
  // unmounted and remounted (or simply hidden) without the scrape noticing.
  const { run, error, starting, setError, launch, applyStatus, hasSession, reset } =
    usePortalSession("wisconsin");
  const handleStart = (livePreview = false) =>
    launch(() => startWisconsinScrape(keyword.trim(), agency.trim(), nigp.trim(), livePreview));

  // Paused is still running as far as this panel is concerned: the form stays
  // locked and Start stays disabled until the run really ends.
  const isRunning = isRunActive(run);
  const hasCriteria = [keyword, agency, nigp].some((v) => v.trim() !== "");

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="Search criteria"
        description="All fields are optional. Leave them blank to capture every current solicitation."
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <Field
            label="Keywords or number"
            value={keyword}
            onChange={setKeyword}
            disabled={isRunning}
            placeholder="e.g. janitorial"
          />
          <Field
            label="Agency"
            value={agency}
            onChange={setAgency}
            disabled={isRunning}
            placeholder="e.g. Dept of Health Services"
          />
          <Field label="NIGP code" value={nigp} onChange={setNigp} disabled={isRunning} placeholder="e.g. 961" />
        </div>
      </Card>

      <LaunchBar summary={hasCriteria ? "Searching with your criteria." : "No criteria set — every current solicitation will be captured."}>
        <div className="flex items-center gap-2">
          <StopButton run={run} onError={setError} />
          <LiveMonitor run={run} portal="wisconsin" />
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
      <SessionActivity portal="wisconsin" />
      {run && <WisconsinResults bids={run.bids} />}
    </div>
  );
}
