"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { getRunScreenshot, type Portal, type RunStatus } from "@/lib/api";
import { Button, RunBadge } from "@/components/ui";
import { stepLabel } from "@/components/RunStatus";

/**
 * Live preview of an in-flight scrape.
 *
 * Renders nothing until a run is actively going (pending/running) — so the
 * button only exists WHILE scraping, never before or after. Clicking it opens a
 * modal that monitors the run in real time: current step, live counts, and a
 * growing event log built from the run status the parent is already polling.
 * For SAM (which exposes a live screenshot endpoint) the modal also streams the
 * browser view. When the run finishes/fails/stops the component unmounts, which
 * hides the button and closes the modal automatically.
 */
export default function LiveMonitor({ run, portal }: { run: RunStatus | null; portal: Portal }) {
  // Which run's preview the user opened. Deriving "open" from the run id (rather
  // than a plain boolean) means the whole component simply unmounts when the run
  // finishes — hiding the button and closing the modal — and a later run never
  // auto-opens from a stale flag.
  const [openedRunId, setOpenedRunId] = useState<string | null>(null);
  const active = !!run && (run.status === "pending" || run.status === "running");

  if (!run || !active) return null;
  const open = openedRunId === run.run_id;

  return (
    <>
      <Button variant="secondary" size="lg" icon={<EyeIcon />} onClick={() => setOpenedRunId(run.run_id)}>
        Live preview
      </Button>
      {open && <LiveMonitorModal run={run} portal={portal} onClose={() => setOpenedRunId(null)} />}
    </>
  );
}

interface LogEntry {
  key: string;
  text: string;
  tone: "step" | "error" | "warning";
}

function LiveMonitorModal({
  run,
  portal,
  onClose,
}: {
  run: RunStatus;
  portal: Portal;
  onClose: () => void;
}) {
  const [log, setLog] = useState<LogEntry[]>([]);
  const [shot, setShot] = useState<string | null>(null);
  const lastStep = useRef<string | null>(null);
  const seenErrors = useRef(0);
  const seenWarnings = useRef(0);
  const logEndRef = useRef<HTMLDivElement | null>(null);

  // Escape closes the modal.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Grow the event log from step changes and any new errors/warnings. The
  // counters de-duplicate across polls so each event is logged exactly once.
  useEffect(() => {
    setLog((prev) => {
      const next = [...prev];
      if (run.step && run.step !== lastStep.current) {
        lastStep.current = run.step;
        next.push({ key: `s${next.length}-${run.step}`, text: stepLabel(run.step), tone: "step" });
      }
      const errors = run.errors ?? [];
      for (let i = seenErrors.current; i < errors.length; i++) {
        next.push({ key: `e${i}`, text: errors[i], tone: "error" });
      }
      seenErrors.current = errors.length;
      const warnings = run.warnings ?? [];
      for (let i = seenWarnings.current; i < warnings.length; i++) {
        next.push({ key: `w${i}`, text: warnings[i], tone: "warning" });
      }
      seenWarnings.current = warnings.length;
      return next;
    });
  }, [run.step, run.errors, run.warnings]);

  // Keep the newest log line in view.
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ block: "end" });
  }, [log]);

  // Stream a live browser frame (any portal) while the modal is open. The
  // shared endpoint returns null until the browser is up, so we just keep the
  // last good frame until a newer one arrives.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const { screenshot } = await getRunScreenshot(run.run_id);
        if (!cancelled && screenshot) setShot(screenshot);
      } catch {
        // no frame yet — ignore
      }
    };
    tick();
    const id = setInterval(tick, 1500);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [run.run_id]);

  if (typeof document === "undefined") return null;

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Live scrape preview"
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/50 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-ink-200 bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between gap-3 border-b border-ink-100 px-5 py-3.5">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-gold-200 border-t-gold-600" />
            <h3 className="text-sm font-semibold text-ink-900">Live preview</h3>
            <span className="truncate text-sm text-ink-500">{stepLabel(run.step)}</span>
          </div>
          <div className="flex items-center gap-2.5">
            <RunBadge status={run.status} />
            <button
              type="button"
              onClick={onClose}
              aria-label="Close live preview"
              className="rounded-md p-1 text-ink-400 transition hover:bg-ink-50 hover:text-ink-700"
            >
              <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
                <path d="M4 4l8 8M12 4l-8 8" strokeLinecap="round" />
              </svg>
            </button>
          </div>
        </header>

        <div className="grid grid-cols-3 divide-x divide-ink-100 border-b border-ink-100 text-center">
          <Stat label="Bids found" value={run.bids_found} />
          <Stat label="Processed" value={run.bids_processed} />
          <Stat label="Documents" value={run.documents_downloaded} />
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          <div className="mb-4">
            <p className="mb-1.5 text-xs font-medium text-ink-500">Live browser</p>
            {shot ? (
              // eslint-disable-next-line @next/next/no-img-element -- transient base64 frame, not a static asset
              <img
                src={`data:image/png;base64,${shot}`}
                alt={`Live ${portal} browser view`}
                className="w-full rounded-lg border border-ink-200"
              />
            ) : (
              <div className="flex h-40 items-center justify-center rounded-lg border border-dashed border-ink-200 text-xs text-ink-400">
                Waiting for the first frame…
              </div>
            )}
          </div>

          <p className="mb-1.5 text-xs font-medium text-ink-500">Activity</p>
          <div className="space-y-1.5 rounded-lg border border-ink-100 bg-ink-50/60 p-3 font-mono text-xs">
            {log.length === 0 ? (
              <p className="text-ink-400">Waiting for the scraper to report progress…</p>
            ) : (
              log.map((entry) => (
                <div
                  key={entry.key}
                  className={
                    entry.tone === "error"
                      ? "text-red-700"
                      : entry.tone === "warning"
                        ? "text-amber-700"
                        : "text-ink-600"
                  }
                >
                  <span className="text-ink-300">
                    {entry.tone === "error" ? "✕ " : entry.tone === "warning" ? "! " : "› "}
                  </span>
                  {entry.text}
                </div>
              ))
            )}
            <div ref={logEndRef} />
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="px-4 py-3">
      <div className="text-xs font-medium text-ink-500">{label}</div>
      <div className="tabular mt-0.5 text-xl font-semibold text-ink-900">{value}</div>
    </div>
  );
}

function EyeIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
      <path d="M1 8s2.5-4.5 7-4.5S15 8 15 8s-2.5 4.5-7 4.5S1 8 1 8Z" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="8" cy="8" r="2" />
    </svg>
  );
}
