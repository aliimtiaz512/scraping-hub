"use client";

import { useEffect, useState } from "react";

import RideMetroResults from "@/components/RideMetroResults";
import RunStatusPanel from "@/components/RunStatus";
import { Card, ErrorBanner, LaunchBar, SegmentedControl, StartButton } from "@/components/ui";
import LiveMonitor from "@/components/LiveMonitor";
import StopButton from "@/components/StopButton";
import RefreshButton from "@/components/RefreshButton";
import ScraperControls from "@/components/ScraperControls";
import SessionActivity from "@/components/SessionActivity";
import { usePortalSession } from "@/lib/usePortalSession";
import {
  getRideMetroAccounts,
  isRunActive,
  startRideMetroScrape,
  type RideMetroAccount,
} from "@/lib/api";

export default function RideMetroPanel() {
  const [accounts, setAccounts] = useState<RideMetroAccount[]>([]);
  const [account, setAccount] = useState<string>("");
  // The run, its log tail, its error banner and the launching flag all live in
  // the global session registry — not here. That is what lets this panel be
  // unmounted and remounted (or simply hidden) without the scrape noticing.
  const { run, error, starting, setError, launch, applyStatus, hasSession, reset } =
    usePortalSession("ridemetro");
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
  // Paused is still running as far as this panel is concerned: the form stays
  // locked and Start stays disabled until the run really ends.
  const isRunning = isRunActive(run);
  const blocked = accounts.length > 0 && !selected?.configured;

  const handleStart = (livePreview = false) =>
    launch(() => startRideMetroScrape(account, livePreview));

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
          <ScraperControls
            run={run}
            onError={setError}
            onStatusChange={applyStatus}
          />
          <RefreshButton run={run} hasSession={hasSession} onReset={reset} />
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
      {/* The run's log, streaming into the panel that launched it. */}
      <SessionActivity portal="ridemetro" />
      {run && <RideMetroResults bids={run.bids} agencies={run.agencies} />}
    </div>
  );
}
