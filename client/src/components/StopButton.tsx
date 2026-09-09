"use client";

import { useState } from "react";

import { Button } from "@/components/ui";
import { isRunActive, stopScrape, type RunStatus } from "@/lib/api";

/**
 * Stops an in-flight run. Like LiveMonitor and ScraperControls, it renders
 * nothing unless a run is actually going (pending/running/paused) — so the
 * button only exists WHILE scraping, never before or after. Clicking it asks the backend to stop; the parent's
 * existing status polling then flips the run to "stopped" and this unmounts.
 *
 * The `onError` hook lets the host panel surface a failure (e.g. the run already
 * finished) in its own error banner.
 */
export default function StopButton({
  run,
  onError,
}: {
  run: RunStatus | null;
  onError?: (message: string) => void;
}) {
  const [stopping, setStopping] = useState(false);
  // Paused counts as active: a parked run is holding a browser and a slot, and
  // stopping it outright — rather than resuming it only to stop it a moment
  // later — is what the backend's STOPPABLE_STATUSES already allows.
  const active = isRunActive(run);
  if (!active) return null;

  const handleStop = async () => {
    setStopping(true);
    try {
      await stopScrape(run.run_id);
    } catch (e) {
      onError?.((e as Error).message);
      setStopping(false); // stayed running — let the user try again
    }
    // On success we deliberately keep `stopping` true: the run is on its way to
    // a terminal state and the component unmounts on the next poll, so the
    // button reads "Stopping…" until it disappears.
  };

  return (
    <Button
      variant="danger"
      size="lg"
      onClick={handleStop}
      loading={stopping}
      icon={!stopping ? <StopIcon /> : undefined}
    >
      {stopping ? "Stopping…" : "Stop"}
    </Button>
  );
}

function StopIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="currentColor" aria-hidden>
      <rect x="4" y="4" width="8" height="8" rx="1.5" />
    </svg>
  );
}
