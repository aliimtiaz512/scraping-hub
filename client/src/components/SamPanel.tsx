"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import RunStatusPanel from "@/components/RunStatus";
import SamResults from "@/components/SamResults";
import { Button, Card, ErrorBanner, LaunchBar, StartButton } from "@/components/ui";
import { getRunStatus, getSamScreenshot, startSamScrape, stopSamScrape, type RunStatus } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;
const SHOT_INTERVAL_MS = 3000;
const inputClass =
  "w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm transition placeholder:text-ink-400 focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/25 disabled:cursor-not-allowed disabled:bg-ink-50 disabled:text-ink-400";

export default function SamPanel() {
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [naics, setNaics] = useState("");
  const [awardNotice, setAwardNotice] = useState(false);
  const [headless, setHeadless] = useState(true);

  const [run, setRun] = useState<RunStatus | null>(null);
  const [shot, setShot] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const shotRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const runIdRef = useRef<string | null>(null);

  const stopTimers = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (shotRef.current) clearInterval(shotRef.current);
    pollRef.current = null;
    shotRef.current = null;
  }, []);

  useEffect(() => stopTimers, [stopTimers]);

  const handleStart = async () => {
    setError(null);
    setStarting(true);
    setShot(null);
    try {
      const naicsCodes = naics.split(/[\s,]+/).map((c) => c.trim()).filter(Boolean);
      const { run_id } = await startSamScrape({
        dateFrom: dateFrom.trim(),
        dateTo: dateTo.trim(),
        naicsCodes,
        awardNotice,
        headless,
      });
      runIdRef.current = run_id;
      setRun(await getRunStatus("sam", run_id));
      stopTimers();
      pollRef.current = setInterval(async () => {
        try {
          const latest = await getRunStatus("sam", run_id);
          setRun(latest);
          if (latest.status === "completed" || latest.status === "failed") stopTimers();
        } catch {
          // transient — keep trying
        }
      }, POLL_INTERVAL_MS);
      // Live browser screenshot while the run is active (best-effort).
      if (!headless) {
        shotRef.current = setInterval(async () => {
          try {
            const { screenshot } = await getSamScreenshot(run_id);
            setShot(screenshot);
          } catch {
            // no frame yet — ignore
          }
        }, SHOT_INTERVAL_MS);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    if (!runIdRef.current) return;
    try {
      await stopSamScrape(runIdRef.current);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const isRunning = run !== null && (run.status === "pending" || run.status === "running");

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="Search filters"
        description="All optional. Narrow by SAM.gov updated-date range and NAICS code; leave blank to sweep every active solicitation. Each scraped bid is evaluated automatically."
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-xs font-semibold text-ink-700">Updated from</label>
            <input type="date" value={dateFrom} disabled={isRunning} onChange={(e) => setDateFrom(e.target.value)} className={inputClass} />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-semibold text-ink-700">Updated to</label>
            <input type="date" value={dateTo} disabled={isRunning} onChange={(e) => setDateTo(e.target.value)} className={inputClass} />
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1.5 block text-xs font-semibold text-ink-700">NAICS codes</label>
            <input
              type="text"
              value={naics}
              disabled={isRunning}
              onChange={(e) => setNaics(e.target.value)}
              placeholder="e.g. 541511, 236220 (comma or space separated)"
              className={inputClass}
            />
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-5">
          <label className="flex items-center gap-2 text-sm text-ink-700">
            <input type="checkbox" checked={awardNotice} disabled={isRunning} onChange={(e) => setAwardNotice(e.target.checked)} className="h-4 w-4 rounded border-ink-300 text-indigo-600 focus:ring-indigo-400" />
            Include Award Notices
          </label>
          <label className="flex items-center gap-2 text-sm text-ink-700">
            <input type="checkbox" checked={headless} disabled={isRunning} onChange={(e) => setHeadless(e.target.checked)} className="h-4 w-4 rounded border-ink-300 text-indigo-600 focus:ring-indigo-400" />
            Run headless (uncheck to watch the browser)
          </label>
        </div>
      </Card>

      <LaunchBar summary="Each bid is scored PURSUE / REJECT by the evaluator as it is scraped.">
        <div className="flex items-center gap-2">
          {isRunning && (
            <Button variant="secondary" onClick={handleStop}>
              Stop
            </Button>
          )}
          <StartButton onClick={handleStart} disabled={starting || isRunning} running={isRunning} starting={starting}>
            Start scrape
          </StartButton>
        </div>
      </LaunchBar>

      {run && <RunStatusPanel run={run} />}

      {isRunning && !headless && shot && (
        <Card title="Live browser">
          {/* eslint-disable-next-line @next/next/no-img-element -- transient base64 frame, not a static asset */}
          <img src={`data:image/png;base64,${shot}`} alt="Live SAM.gov browser view" className="w-full rounded-lg border border-ink-200" />
        </Card>
      )}

      {run && <SamResults bids={run.bids} />}
    </div>
  );
}
