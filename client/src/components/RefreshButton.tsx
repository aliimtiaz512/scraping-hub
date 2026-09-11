"use client";

import { useState } from "react";

import { Button } from "@/components/ui";
import { isRunActive, stopScrape, type RunStatus } from "@/lib/api";

/**
 * Resets a panel's slot back to its fresh, never-launched state — clears the
 * streamed log, the last run (and with it the results table and progress
 * counters, which panels derive from `run`), and any error banner.
 *
 * Sits in the same toolbar as Live preview and Pause/Resume rather than
 * tucked into a menu, because "start this tab over" is exactly the kind of
 * thing a user reaches for right after staring at a stale completed run from
 * hours ago — it should be as reachable as Stop.
 *
 * Nothing about the underlying data is at risk: resetting only clears the
 * local view. The rows a finished run produced stay on the server and stay
 * reachable from the Downloads page regardless of what this button does.
 *
 * A run still in flight is the one case that needs a confirmation, because
 * clearing the slot out from under it would not actually stop it — the
 * engine's discovery poll would simply re-adopt the same run into the empty
 * slot on its next tick. So while active, this stops the run first and only
 * then resets, and warns the user that's what "Refresh" is about to do.
 */
export default function RefreshButton({
  run,
  hasSession,
  onReset,
}: {
  run: RunStatus | null;
  /** False for a slot that has never been launched — nothing to clear. */
  hasSession: boolean;
  onReset: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const active = isRunActive(run);

  if (!hasSession) {
    return (
      <Button variant="secondary" size="lg" icon={<RefreshIcon />} disabled title="Nothing to refresh yet">
        Refresh
      </Button>
    );
  }

  const handleClick = () => {
    if (active) {
      setConfirming(true);
      return;
    }
    onReset();
  };

  const handleConfirm = async () => {
    if (!run) {
      onReset();
      setConfirming(false);
      return;
    }
    setBusy(true);
    try {
      await stopScrape(run.run_id);
    } catch {
      // Already stopped, or finished between the click and this call — either
      // way there is nothing left running, so the reset below still applies.
    }
    setBusy(false);
    setConfirming(false);
    onReset();
  };

  return (
    <>
      <Button
        variant="secondary"
        size="lg"
        onClick={handleClick}
        icon={<RefreshIcon />}
        title={active ? "Abort the active run and clear this tab" : "Clear this tab's logs and results"}
      >
        Refresh
      </Button>

      {confirming && (
        <div
          role="dialog"
          aria-modal
          aria-labelledby="refresh-confirm-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 px-4"
          onClick={() => !busy && setConfirming(false)}
        >
          <div
            className="w-full max-w-sm rounded-xl border border-ink-200 bg-white p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="refresh-confirm-title" className="font-display text-base text-ink-900">
              Abort the running scrape?
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-ink-600">
              This scraper is currently running. Refreshing will stop the active run and clear
              this tab&apos;s logs and results. Proceed?
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="ghost" size="md" onClick={() => setConfirming(false)} disabled={busy}>
                Cancel
              </Button>
              <Button variant="danger" size="md" onClick={handleConfirm} loading={busy}>
                {busy ? "Stopping…" : "Stop & refresh"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function RefreshIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="currentColor" aria-hidden>
      <path d="M13.65 2.35a.75.75 0 0 0-1.3.5v1.27A6.5 6.5 0 1 0 14.5 8a.75.75 0 0 0-1.5 0 5 5 0 1 1-1.64-3.71h-1.6a.75.75 0 0 0 0 1.5h3a.75.75 0 0 0 .75-.75v-3a.74.74 0 0 0-.86-.69Z" />
    </svg>
  );
}
