"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import RunStatusPanel from "@/components/RunStatus";
import WisconsinResults from "@/components/WisconsinResults";
import { ErrorBanner, SectionLabel, StartButton } from "@/components/ui";
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

  return (
    <div className="space-y-6">
      <p className="text-sm leading-relaxed text-slate-400">
        Searches the Wisconsin eSupplier Current Solicitations and saves every matching
        event (number, reference, type, title, agency, status, due date) to the database and
        an Excel sheet. Leave the fields blank to capture all current solicitations.
      </p>

      {error && <ErrorBanner message={error} />}

      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Keywords or Number" value={keyword} onChange={setKeyword} disabled={isRunning} placeholder="e.g. janitorial" />
        <Field label="Agency" value={agency} onChange={setAgency} disabled={isRunning} placeholder="e.g. Dept of Health Services" />
        <Field label="NIGP Code" value={nigp} onChange={setNigp} disabled={isRunning} placeholder="e.g. 961" />
      </div>

      <StartButton onClick={handleStart} disabled={starting || isRunning} running={isRunning} starting={starting}>
        Search &amp; scrape
      </StartButton>

      {run && <RunStatusPanel run={run} />}
      {run && <WisconsinResults bids={run.bids} />}
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  disabled,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  return (
    <div>
      <SectionLabel>{label}</SectionLabel>
      <input
        type="text"
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-xl border border-white/10 bg-slate-950/50 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-emerald-400/40 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
      />
    </div>
  );
}
