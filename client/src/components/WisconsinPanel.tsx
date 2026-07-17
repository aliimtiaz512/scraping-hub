"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import RunStatusPanel from "@/components/RunStatus";
import WisconsinResults from "@/components/WisconsinResults";
import { Card, ErrorBanner, Field, LaunchBar, StartButton } from "@/components/ui";
import { getRunStatus, startWisconsinScrape, type RunStatus } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

export default function WisconsinPanel() {
  const [keyword, setKeyword] = useState("");
  const [agency, setAgency] = useState("");
  const [nigp, setNigp] = useState("");
  const [run, setRun] = useState<RunStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

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
      const { run_id } = await startWisconsinScrape(keyword.trim(), agency.trim(), nigp.trim());
      const status = await getRunStatus("wisconsin", run_id);
      setRun(status);
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const latest = await getRunStatus("wisconsin", run_id);
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
        <StartButton onClick={handleStart} disabled={starting || isRunning} running={isRunning} starting={starting}>
          Search &amp; scrape
        </StartButton>
      </LaunchBar>

      {run && <RunStatusPanel run={run} />}
      {run && <WisconsinResults bids={run.bids} />}
    </div>
  );
}
