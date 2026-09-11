"use client";

import { useEffect, useRef } from "react";

import {
  getJobLogs,
  getJobs,
  getRunStatus,
  getSweepRunStatus,
  isRunActive,
} from "@/lib/api";
import {
  sessionsWithRuns,
  adopt,
  appendLogs,
  expireStaleSessions,
  getSession,
  putJobsFeed,
  putRun,
  restore,
  slotForScraper,
  type Session,
} from "@/lib/sessions";

const TICK_MS = 3000;
/**
 * Ticks a finished run is still polled for.
 *
 * A run reaches a terminal status before it has finished with itself: the rows
 * are written, the archive packaged and `partial_results` set moments later. If
 * the engine stopped the instant the status went terminal, the panel's Download
 * button would only appear on the next reload — which is the trip this whole
 * change exists to remove. Ten ticks is ~30s: long enough for the flush, short
 * enough that a finished console settles to one request per tick.
 */
const GRACE_TICKS = 10;

/**
 * The one thing in the console that watches runs.
 *
 * Mounted by the console shell — which is a layout, so it outlives every page
 * under it — this is what makes a scrape independent of the tab that launched
 * it. It discovers runs, polls their status, tails their logs and writes the
 * lot into the session registry. Panels never poll; they read.
 *
 * That inversion is the whole fix. Before, each panel bound its own interval on
 * mount and cleared it on unmount, so navigating away from a portal stopped the
 * console watching the run — the worker carried on alone and the panel came back
 * empty. Now the watching belongs to the shell, so SAM keeps streaming while you
 * are looking at SEPTA, and switching back is a render, not a restart.
 *
 * The transport is HTTP polling, because that is what the API offers: there is
 * no SSE or WebSocket endpoint on the server. What matters for the defect is not
 * the transport but its lifetime — one set of listeners at the top of the tree,
 * bound once, rather than a set per panel bound and unbound by navigation. If
 * the backend grows a stream later, it is this component that opens it and
 * nothing else has to change.
 */
export default function SessionEngine() {
  // The engine is a singleton; a second instance would double every poll.
  const running = useRef(false);
  // Ticks left on each finished run, counted down as it is polled out.
  const grace = useRef<Map<string, number>>(new Map());

  useEffect(() => {
    if (running.current) return;
    running.current = true;

    // Runs from the last page view, so a reload lands on a live panel rather
    // than an empty one. Their contents are re-fetched, never revived.
    restore();

    let cancelled = false;

    /** One pass: discover what is in flight, refresh what we hold, then sweep
     *  out anything that finished long enough ago to have gone stale. Swept
     *  last so a slot doesn't get purged and re-discovered in the same tick. */
    const tick = async () => {
      await Promise.all([discover(), refresh()]);
      expireStaleSessions();
    };

    /**
     * Adopt anything the registry does not know about. This is what puts a run
     * back in its panel after a reload, and what shows a run started in another
     * browser tab — the console's view of "what is running" is the server's.
     */
    const discover = async () => {
      try {
        const { jobs, capacity } = await getJobs(true);
        if (cancelled) return;
        putJobsFeed(jobs, capacity);
        for (const job of jobs) {
          const { portal, kind } = slotForScraper(job.scraper);
          adopt(portal, job.run_id, kind);
        }
      } catch {
        // The API being briefly unreachable is not worth shouting about — the
        // next tick either recovers or the user has bigger problems.
      }
    };

    /**
     * Refresh what the registry holds: everything in flight, plus anything that
     * has just finished, for a few ticks more. Once the grace runs out the run
     * drops off and the engine is idle again — a console with nothing running
     * makes one request a tick, the discovery poll.
     */
    const refresh = async () => {
      const sessions = sessionsWithRuns().filter((s) => {
        if (!s.run || isRunActive(s.run)) return true;
        const left = grace.current.get(s.runId) ?? GRACE_TICKS;
        grace.current.set(s.runId, left - 1);
        return left > 0;
      });
      await Promise.all(sessions.map((s) => Promise.all([pollStatus(s), pollLogs(s)])));
    };

    const pollStatus = async (session: Session) => {
      try {
        const latest =
          session.kind === "sweep"
            ? await getSweepRunStatus(session.runId)
            : await getRunStatus(session.portal, session.runId);
        if (cancelled) return;
        // The slot may have been re-launched or cleared while this was in the
        // air; a late answer for a run nobody is watching any more must not
        // overwrite the new one.
        if (getSession(session.portal)?.runId !== session.runId) return;
        putRun(session.portal, latest);
      } catch {
        // transient — the next tick retries
      }
    };

    /** The log tail, asked for incrementally — each poll asks only for what is
     *  new, so a long run costs the same as a short one. */
    const pollLogs = async (session: Session) => {
      try {
        const { lines, seq } = await getJobLogs(session.runId, session.logSeq);
        if (cancelled) return;
        if (getSession(session.portal)?.runId !== session.runId) return;
        appendLogs(session.portal, lines, seq);
      } catch {
        // transient — the next tick retries
      }
    };

    // Browsers throttle `setInterval` in a backgrounded tab, so a slot can sit
    // well past its TTL by the time the timer above next fires. Sweeping again
    // the moment the tab is looked at means the stale-to-reset gap is bounded
    // by how long the user was away, not by the throttled tick.
    const onVisible = () => {
      if (document.visibilityState === "visible") expireStaleSessions();
    };
    document.addEventListener("visibilitychange", onVisible);

    void tick();
    const timer = setInterval(() => void tick(), TICK_MS);
    return () => {
      cancelled = true;
      running.current = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // Mounted once for the life of the console shell.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return null;
}
