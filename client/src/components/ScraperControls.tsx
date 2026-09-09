"use client";

import { useState } from "react";

import { Button } from "@/components/ui";
import { isRunActive, pauseScrape, resumeScrape, type RunStatus } from "@/lib/api";

/**
 * Pause / Resume for the run a panel is watching — the primary-toolbar control,
 * sitting beside Live preview.
 *
 * These used to live only as 20px chips inside the active-jobs bar at the foot
 * of the console, which is the wrong place for them: the person who wants to
 * park a run is looking at the panel that launched it, not hunting a collapsed
 * footer. This renders at the same size as Live preview and Stop so the three
 * controls of a run in flight read as one set.
 *
 * Like LiveMonitor and StopButton it renders nothing unless a run is actually
 * in flight, so the toolbar is bare before the first launch and after the run
 * ends. A parked run counts as in flight — that is the state whose only exit is
 * this button.
 *
 * `onStatusChange` is what keeps the rest of the console honest without waiting
 * on a poll: the API sets the run's status synchronously (the worker parks at
 * its next checkpoint, but the run is already marked), so handing the new status
 * straight back to the panel means the badge, the form's disabled state and this
 * button all flip on the click rather than up to a poll interval later.
 */
export default function ScraperControls({
  run,
  onError,
  onStatusChange,
}: {
  run: RunStatus | null;
  onError?: (message: string) => void;
  /** The status the server has just moved the run to — apply it locally. */
  onStatusChange?: (status: RunStatus["status"]) => void;
}) {
  const [busy, setBusy] = useState(false);

  if (!isRunActive(run)) return null;

  const parked = run.status === "paused";
  // A run that has been accepted but has not started has no worker to park, and
  // the API answers 409 — so offer nothing until it is actually executing.
  if (!parked && run.status !== "running") return null;

  const handleClick = async () => {
    setBusy(true);
    try {
      if (parked) {
        await resumeScrape(run.run_id);
        onStatusChange?.("running");
      } else {
        await pauseScrape(run.run_id);
        onStatusChange?.("paused");
      }
    } catch (e) {
      // e.g. the run finished between the render and the click — say so and
      // leave the button as it was; the next poll settles the real state.
      onError?.((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Button
      variant={parked ? "success" : "warning"}
      size="lg"
      onClick={handleClick}
      loading={busy}
      icon={busy ? undefined : parked ? <PlayIcon /> : <PauseIcon />}
      title={
        parked
          ? "Continue from the record after the last one finished — nothing is re-collected."
          : "Hold at the next record. Keeps the browser and the slot; frees the network and CPU for another run."
      }
    >
      {busy
        ? parked
          ? "Resuming…"
          : "Pausing…"
        : parked
          ? "Resume scraper"
          : "Pause scraper"}
    </Button>
  );
}

function PauseIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="currentColor" aria-hidden>
      <rect x="4" y="3" width="3" height="10" rx="1" />
      <rect x="9" y="3" width="3" height="10" rx="1" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="currentColor" aria-hidden>
      <path d="M5 3.5a.75.75 0 0 1 1.13-.65l6 4.5a.75.75 0 0 1 0 1.3l-6 4.5A.75.75 0 0 1 5 12.5v-9Z" />
    </svg>
  );
}
