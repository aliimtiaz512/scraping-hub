"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import RideMetroResults from "@/components/RideMetroResults";
import RunStatusPanel from "@/components/RunStatus";
import { Card, ErrorBanner, LaunchBar, SegmentedControl, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import {
  getRideMetroAccounts,
  getRunStatus,
  startRideMetroScrape,
  type RideMetroAccount,
  type RunStatus,
} from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

export default function RideMetroPanel() {
  const [accounts, setAccounts] = useState<RideMetroAccount[]>([]);
  const [account, setAccount] = useState<string>("");
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

  // The accounts and which is configured are the server's to say — the picker
  // is built from what it reports rather than from a list hardcoded here, so an
  // account added to .env shows up without a frontend change.
  useEffect(() => {
    let cancelled = false;
    getRideMetroAccounts()
      .then(({ accounts: fetched, default: fallback }) => {
        if (cancelled) return;
        setAccounts(fetched);
        // Land on a usable account: the server's default if it can run, else
        // the first that can, else the default so the picker still has a value.
        const usable = fetched.find((a) => a.key === fallback && a.configured)
          ?? fetched.find((a) => a.configured);
        setAccount(usable?.key ?? fallback);
      })
      .catch((e) => !cancelled && setError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, []);

  const selected = accounts.find((a) => a.key === account);
  const isRunning = run !== null && (run.status === "pending" || run.status === "running");
  const blocked = accounts.length > 0 && !selected?.configured;

  const handleStart = async (livePreview = false) => {
    setError(null);
    setStarting(true);
    try {
      const { run_id } = await startRideMetroScrape(account, livePreview);
      const status = await getRunStatus("ridemetro", run_id);
      setRun(status);
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const latest = await getRunStatus("ridemetro", run_id);
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

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="Account"
        description="Which login to run as. The two accounts belong to different Euna Supplier Networks, so this decides which agencies the run sweeps."
      >
        <SegmentedControl
          name="ridemetro-account"
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
        {blocked && (
          <p className="mt-3 text-xs leading-relaxed text-red-700">
            {selected?.label ?? "This account"} has no credentials on the server, so a run cannot
            sign in. Add {selected?.username_env} and {selected?.password_env} to{" "}
            <code className="font-mono">server/.env</code> and restart the API.
          </p>
        )}
      </Card>

      <LaunchBar
        summary={
          selected?.configured
            ? `Runs as ${selected.label} — sweeps every agency in that network whose registration is Complete, and captures their open public opportunities.`
            : "Choose a configured account to run."
        }
      >
        <div className="flex items-center gap-2">
          <StopButton run={run} onError={setError} />
          <LiveMonitor run={run} portal="ridemetro" />
          <StartButton
            onClick={() => handleStart()}
            disabled={starting || isRunning || blocked || !account}
            running={isRunning}
            starting={starting}
          >
            Start scrape
          </StartButton>
        </div>
      </LaunchBar>

      {run && <RunStatusPanel run={run} />}
      {run && <RideMetroResults bids={run.bids} agencies={run.agencies} />}
    </div>
  );
}
