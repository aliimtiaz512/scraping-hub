"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Card, ErrorBanner, LaunchBar, RunBadge, StartButton } from "@/components/ui";
import { getRunStatus, startCalEProcureScrape, type RunStatus } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

/**
 * Cal eProcure is being built one step at a time. The only functionality so far
 * is login verification: the run signs in to the supplier portal and confirms
 * the session, and this panel reports the outcome. Search + export come next.
 */
export default function CalEProcurePanel() {
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
      const { run_id } = await startCalEProcureScrape();
      setRun(await getRunStatus("caleprocure", run_id));
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const latest = await getRunStatus("caleprocure", run_id);
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
  const loggedIn = run?.status === "completed" && run.login_ok === true;

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="Login verification"
        description="This first step signs in to Cal eProcure with the credentials in server/.env and confirms the session. Nothing is scraped yet — solicitation search and Excel export are added next."
      >
        <p className="text-sm leading-relaxed text-ink-600">
          Press <span className="font-medium text-ink-900">Test login</span> to open the portal, sign in,
          and verify the session. With <span className="font-mono text-xs">HEADLESS=false</span> in{" "}
          <span className="font-mono text-xs">server/.env</span>, you can watch the browser drive it.
        </p>
      </Card>

      <LaunchBar summary="Signs in to caleprocure.ca.gov and confirms the session.">
        <StartButton onClick={handleStart} disabled={starting || isRunning} running={isRunning} starting={starting}>
          Test login
        </StartButton>
      </LaunchBar>

      {run && (
        <section className="overflow-hidden rounded-xl border border-ink-200 bg-white shadow-sm">
          <header className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-100 px-5 py-3.5">
            <h3 className="text-sm font-semibold text-ink-900">Login status</h3>
            <div className="flex items-center gap-2.5">
              <span className="font-mono text-xs text-ink-400">{run.run_id}</span>
              <RunBadge status={run.status} />
            </div>
          </header>

          {isRunning && (
            <div className="flex items-center gap-2.5 px-5 py-4 text-sm font-medium text-ink-700">
              <span className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-gold-200 border-t-gold-600" />
              Signing in to Cal eProcure…
            </div>
          )}

          {loggedIn && (
            <div className="p-5">
              <SuccessBanner
                title="Successfully logged in"
                detail={
                  run.landing_title
                    ? `Signed in and landed on “${run.landing_title}”.`
                    : "Signed in and confirmed the session."
                }
              />
            </div>
          )}

          {run.status === "failed" && (
            <div className="space-y-3 p-5">
              {run.errors.length > 0 ? (
                run.errors.map((message, i) => <ErrorBanner key={i} message={message} />)
              ) : (
                <ErrorBanner message="Login failed. Check the Cal eProcure credentials in server/.env and try again." />
              )}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

/** Green counterpart to ErrorBanner — a confirmed, successful outcome. */
function SuccessBanner({ title, detail }: { title: string; detail?: string }) {
  return (
    <div
      role="status"
      className="flex items-start gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800"
    >
      <svg viewBox="0 0 20 20" fill="currentColor" className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" aria-hidden>
        <path
          fillRule="evenodd"
          d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm3.7-9.8a.75.75 0 1 0-1.2-.9l-3.19 4.25-1.55-1.55a.75.75 0 0 0-1.06 1.06l2.2 2.2a.75.75 0 0 0 1.13-.08l3.72-4.98Z"
          clipRule="evenodd"
        />
      </svg>
      <div className="min-w-0">
        <p className="font-medium">{title}</p>
        {detail && <p className="mt-0.5 text-emerald-700">{detail}</p>}
      </div>
    </div>
  );
}
