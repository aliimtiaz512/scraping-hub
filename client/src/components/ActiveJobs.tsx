"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getJobLogs, getJobs, stopScrape, type Job, type JobCapacity, type JobLogLine } from "@/lib/api";
import { PORTALS } from "@/lib/portals";

const POLL_INTERVAL_MS = 3000;

/**
 * Every scrape in flight, on every portal, wherever you are in the console.
 *
 * Mounted in the console layout rather than on a page, so it keeps reporting
 * while you move between portals — which is the point: a run belongs to the
 * server, not to the page that started it. One `GET /runs?active=true` covers
 * all portals, so this costs a single poll no matter how many are going.
 *
 * Collapsed it is a one-line bar; expanded it lists each job with its step, its
 * elapsed time, a Stop control, and an optional live log tail.
 */
export default function ActiveJobs() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [capacity, setCapacity] = useState<JobCapacity | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [openLog, setOpenLog] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const poll = useCallback(async () => {
    try {
      const { jobs: fetched, capacity: cap } = await getJobs(true);
      setJobs(fetched);
      setCapacity(cap);
      setError(null);
    } catch {
      // The API being briefly unreachable is not worth shouting about — the
      // next tick will either recover or the user has bigger problems.
    }
  }, []);

  useEffect(() => {
    poll();
    const timer = setInterval(poll, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [poll]);

  // Nothing running: the bar stays out of the way entirely.
  if (jobs.length === 0) return null;

  const running = jobs.filter((j) => j.status === "running").length;
  const queued = jobs.filter((j) => j.status === "queued").length;

  const handleStop = async (job: Job) => {
    try {
      await stopScrape(job.run_id);
      poll();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="sticky bottom-0 z-30 border-t border-ink-200 bg-white/95 shadow-[0_-2px_12px_rgba(15,23,42,0.06)] backdrop-blur">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-3 px-5 py-2.5 text-left transition hover:bg-ink-50 sm:px-8"
      >
        <span className="relative flex h-2 w-2 shrink-0">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-gold-400 opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-gold-500" />
        </span>
        <span className="text-sm font-medium text-ink-900">
          {running} running{queued > 0 && <span className="text-ink-500"> · {queued} queued</span>}
        </span>
        {capacity && (
          <span className="text-xs text-ink-400">
            {capacity.running}/{capacity.capacity} slots in use
          </span>
        )}
        <span className="ml-auto text-xs text-ink-500">{expanded ? "Hide" : "Show"}</span>
      </button>

      {expanded && (
        <div className="max-h-80 overflow-y-auto border-t border-ink-100 px-5 py-3 sm:px-8">
          {error && <p className="mb-2 text-xs text-red-600">{error}</p>}
          <ul className="space-y-1.5">
            {jobs.map((job) => (
              <JobRow
                key={job.run_id}
                job={job}
                logOpen={openLog === job.run_id}
                onToggleLog={() => setOpenLog(openLog === job.run_id ? null : job.run_id)}
                onStop={() => handleStop(job)}
              />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function JobRow({
  job,
  logOpen,
  onToggleLog,
  onStop,
}: {
  job: Job;
  logOpen: boolean;
  onToggleLog: () => void;
  onStop: () => void;
}) {
  const queued = job.status === "queued";
  const portal = PORTALS.find((p) => p.key === job.scraper);
  // Whichever field the owning portal fills in — its name for what this run is
  // working on. Portals differ, so the first that exists wins.
  const subject =
    job.account_label ?? job.niche_label ?? job.filter_label ?? job.module ?? job.search ?? "";

  return (
    <li className="rounded-lg border border-ink-200 bg-white px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${queued ? "bg-ink-300" : "bg-emerald-500"}`}
          aria-hidden
        />
        <span className="text-sm font-medium text-ink-900">{portal?.label ?? job.scraper}</span>
        {subject && <span className="truncate text-xs text-ink-500">{subject}</span>}
        <span className="truncate font-mono text-xs text-ink-500">{job.step}</span>
        <span className="tabular ml-auto text-xs text-ink-400">
          {job.bids_found > 0 && `${job.bids_found} found · `}
          <Elapsed since={job.started_at} paused={queued} />
        </span>
        <button
          type="button"
          onClick={onToggleLog}
          className="rounded border border-ink-200 px-2 py-0.5 text-xs text-ink-600 transition hover:bg-ink-50"
        >
          {logOpen ? "Hide log" : "Log"}
        </button>
        <button
          type="button"
          onClick={onStop}
          className="rounded border border-red-200 px-2 py-0.5 text-xs font-medium text-red-700 transition hover:bg-red-50"
        >
          {/* A queued job has not started, so ending it is a cancel, not a stop. */}
          {queued ? "Cancel" : "Stop"}
        </button>
      </div>
      {logOpen && <LogTail runId={job.run_id} />}
    </li>
  );
}

/** The run's log, tailed incrementally — each poll asks only for what is new. */
function LogTail({ runId }: { runId: string }) {
  const [lines, setLines] = useState<JobLogLine[]>([]);
  const seq = useRef(0);
  const box = useRef<HTMLPreElement>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const { lines: fresh, seq: latest } = await getJobLogs(runId, seq.current);
        if (cancelled || fresh.length === 0) return;
        seq.current = latest;
        setLines((prev) => [...prev, ...fresh].slice(-200));
      } catch {
        // transient — the next tick retries
      }
    };
    tick();
    const timer = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [runId]);

  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight });
  }, [lines]);

  return (
    <pre
      ref={box}
      className="mt-2 max-h-40 overflow-y-auto rounded bg-ink-900 px-3 py-2 font-mono text-[11px] leading-relaxed text-ink-100"
    >
      {lines.length === 0
        ? "waiting for output…"
        : lines.map((line) => `${line.level.padEnd(7)} ${line.message}`).join("\n")}
    </pre>
  );
}

/** Wall-clock time since the run was accepted, ticking once a second. */
function Elapsed({ since, paused }: { since: string; paused?: boolean }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  if (paused) return <>queued</>;
  const seconds = Math.max(0, Math.floor((now - new Date(since).getTime()) / 1000));
  const minutes = Math.floor(seconds / 60);
  return <>{minutes > 0 ? `${minutes}m ${seconds % 60}s` : `${seconds}s`}</>;
}
