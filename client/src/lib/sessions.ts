"use client";

import { useCallback, useSyncExternalStore } from "react";

import {
  isRunActive,
  isTerminalStatus,
  SWEEP_SCRAPER,
  type Job,
  type JobCapacity,
  type JobLogLine,
  type Portal,
  type RunStatus,
} from "@/lib/api";

/**
 * The console's scrape sessions, held outside React.
 *
 * A run belongs to the server, not to the page that started it — but until now
 * the console only knew that in the footer bar. Every panel kept its run in its
 * own `useState` and its own `setInterval`, so moving from SAM to SEPTA and back
 * unmounted the SAM panel, cleared its poller and wiped the run: the worker kept
 * going, and the tab that launched it came back blank, with the output only
 * reachable from the global Downloads page.
 *
 * This registry is the fix. One slot per portal, living at module scope, fed by
 * a single engine (see SessionEngine) that runs for as long as the console is
 * open. Panels read their slot and render it; nothing they do — mounting,
 * unmounting, navigating — starts or stops any polling.
 *
 * MyFlorida's ad-status sweep runs on its own endpoint and its own run key, so a
 * session carries the `kind` that says which status endpoint owns it. It still
 * occupies the MyFlorida slot: one portal, one browser, one session.
 */

export type SessionKind = "portal" | "sweep";

export interface Session {
  portal: Portal;
  runId: string;
  kind: SessionKind;
  /** Latest full status from the run's own endpoint — the panel's whole view. */
  run: RunStatus | null;
  /** Streaming log tail, newest last, capped so a long run cannot grow forever. */
  logs: JobLogLine[];
  /** Highest log sequence number seen, so each poll asks only for what is new. */
  logSeq: number;
  /** A launch or control failure worth showing in the panel's error banner. */
  error: string | null;
  /** True between pressing Start and the server handing back a run id. */
  starting: boolean;
  /** Wall-clock ms when this slot was last touched, for the reload handoff. */
  updatedAt: number;
  /**
   * Wall-clock ms when this run first reached a terminal status, or null while
   * it is still in flight (or has never run). This is what the TTL sweep reads
   * — a slot that finished and was then left alone, not one merely idle before
   * its first launch.
   */
  completedAt: number | null;
}

/** How much log tail a slot keeps. Enough to scroll back through a stage. */
const LOG_LIMIT = 500;

/** Where the slot→run mapping survives a page reload. */
const STORAGE_KEY = "scraping-hub.sessions.v1";

/** An empty slot: a portal the user has opened but never launched. */
function blank(portal: Portal): Session {
  return {
    portal,
    runId: "",
    kind: "portal",
    run: null,
    logs: [],
    logSeq: 0,
    error: null,
    starting: false,
    updatedAt: Date.now(),
    completedAt: null,
  };
}

// -- the store ---------------------------------------------------------------

type Slots = Readonly<Partial<Record<Portal, Session>>>;

let slots: Slots = {};
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

/** Replace one slot and notify. Identity changes only for the slot touched, so
 *  a SEPTA poll does not re-render the SAM panel. */
function write(portal: Portal, next: Session | null) {
  const copy: Partial<Record<Portal, Session>> = { ...slots };
  if (next) copy[portal] = { ...next, updatedAt: Date.now() };
  else delete copy[portal];
  slots = copy;
  persist();
  emit();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getSlots(): Slots {
  return slots;
}

export function getSession(portal: Portal): Session | undefined {
  return slots[portal];
}

// -- reload persistence ------------------------------------------------------

/**
 * Only the identity of each slot's run is persisted — never the run's contents.
 *
 * The server is the source of truth for status, counts, bids and logs, and it
 * still has them; re-fetching on load is both smaller and fresher than reviving
 * a snapshot that went stale while the tab was closed. What the browser has to
 * remember is the one thing the server cannot tell it: which run this console
 * considers to be occupying which slot.
 */
interface Persisted {
  portal: Portal;
  runId: string;
  kind: SessionKind;
}

function persist() {
  if (typeof window === "undefined") return;
  const rows: Persisted[] = Object.values(slots)
    .filter((s): s is Session => !!s && !!s.runId)
    .map((s) => ({ portal: s.portal, runId: s.runId, kind: s.kind }));
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(rows));
  } catch {
    // Private mode, or a full quota. The registry still works for this page
    // view; only the reload handoff is lost, and the jobs poll re-adopts any
    // run that is still going anyway.
  }
}

/** Re-seed the registry from the last page view. Called once by the engine. */
export function restore() {
  if (typeof window === "undefined") return;
  let rows: Persisted[] = [];
  try {
    rows = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "[]");
  } catch {
    return;
  }
  if (!Array.isArray(rows)) return;
  const copy: Partial<Record<Portal, Session>> = { ...slots };
  for (const row of rows) {
    if (!row?.portal || !row?.runId || copy[row.portal]) continue;
    copy[row.portal] = {
      ...blank(row.portal),
      runId: row.runId,
      kind: row.kind === "sweep" ? "sweep" : "portal",
    };
  }
  slots = copy;
  emit();
}

// -- actions -----------------------------------------------------------------

/** Start button pressed: clear the last error and show the launching state. */
export function beginLaunch(portal: Portal) {
  write(portal, { ...(slots[portal] ?? blank(portal)), starting: true, error: null });
}

/**
 * The server accepted a run and named it. From here the engine owns it.
 *
 * The previous run's status and logs are dropped rather than appended to: this
 * is a new run in the slot, and a log tail that ran two runs together would be
 * unreadable.
 */
export function attachRun(portal: Portal, runId: string, kind: SessionKind = "portal") {
  write(portal, { ...blank(portal), runId, kind, starting: false });
}

/** A launch that never got a run id. */
export function failLaunch(portal: Portal, message: string) {
  write(portal, { ...(slots[portal] ?? blank(portal)), starting: false, error: message });
}

export function setSessionError(portal: Portal, message: string | null) {
  write(portal, { ...(slots[portal] ?? blank(portal)), error: message });
}

/** Fresh status from the engine's poll. */
export function putRun(portal: Portal, run: RunStatus) {
  const current = slots[portal] ?? blank(portal);
  // Stamped the first tick a run is seen terminal, kept as-is on every tick
  // after (so re-polling a finished run does not keep pushing its expiry back),
  // and cleared if the slot is ever handed a run that is not terminal — the
  // only way that happens is a fresh launch, which already goes through
  // `attachRun`'s blank slot, but this keeps the invariant true regardless of
  // how a `RunStatus` arrives here.
  const completedAt = isTerminalStatus(run.status) ? (current.completedAt ?? Date.now()) : null;
  write(portal, { ...current, runId: run.run_id, run, starting: false, completedAt });
}

/**
 * Apply a status the console itself just caused (pause, resume), without waiting
 * for the next poll. The engine's next tick overwrites it with the server's own
 * answer, which is the same one — the API sets the status synchronously.
 */
export function patchRunStatus(portal: Portal, status: RunStatus["status"]) {
  const current = slots[portal];
  if (!current?.run) return;
  write(portal, { ...current, run: { ...current.run, status } });
}

/** New log lines from the tail poll. */
export function appendLogs(portal: Portal, lines: JobLogLine[], seq: number) {
  const current = slots[portal];
  if (!current || lines.length === 0) return;
  write(portal, {
    ...current,
    logs: [...current.logs, ...lines].slice(-LOG_LIMIT),
    logSeq: Math.max(seq, current.logSeq),
  });
}

/**
 * Adopt a run the console did not launch in this page view — one still going
 * after a reload, or started from another browser tab.
 *
 * Only ever fills an empty slot. A slot already holding a run is left alone:
 * the panel's own launch is the authority on what it is watching.
 */
export function adopt(portal: Portal, runId: string, kind: SessionKind) {
  const current = slots[portal];
  if (current?.runId) return;
  write(portal, { ...blank(portal), runId, kind });
}

/**
 * Reset one scraper's slot to its fresh, never-launched state — the panel's
 * Refresh affordance, and what the TTL sweep below calls once a finished slot
 * has gone stale. Flushes the streamed logs, the last status (and with it the
 * results table and progress counters, which panels derive from `run`), and
 * the error banner, all in one write so there is no frame where only some of
 * them are gone.
 *
 * This never touches the server: a run still in flight keeps running, and the
 * next `discover()` tick will simply re-adopt it into the now-empty slot
 * unless the caller has already stopped it. That's deliberate — this action
 * owns the *local view*, not the run's lifecycle. Callers that mean to abort
 * an active run (the confirmed Refresh click) call `stopScrape` themselves
 * before resetting.
 *
 * Deleting the slot rather than writing `blank(portal)` matters for one thing:
 * a deleted slot has no `runId`, so it is never mistaken for "a run finished
 * and its output is empty" — it reads as "nothing has been launched here",
 * identical to a portal the user has never opened.
 */
export function resetScraperState(portal: Portal) {
  write(portal, null);
}

/**
 * How long a finished slot is kept on screen before it is swept — long enough
 * to walk away and come back, short enough that a console left open overnight
 * doesn't accumulate a wall of finished runs nobody is looking at. The rows
 * themselves are never at risk: they live on the server and stay reachable
 * from the Downloads page regardless of what this sweep does to the slot.
 */
export const SESSION_TTL_MS = 2 * 60 * 60 * 1000;

/**
 * Reset every completed slot that has sat untouched past `SESSION_TTL_MS`.
 *
 * Called by the engine on its regular tick and again on tab focus, so a
 * console left in a background tab catches up the moment it's looked at
 * rather than waiting for the next interval fire (browsers throttle timers in
 * inactive tabs, so relying on the interval alone could leave a stale slot
 * showing for a while after the user returns).
 */
export function expireStaleSessions(now: number = Date.now()) {
  for (const session of Object.values(slots)) {
    if (!session) continue;
    if (session.completedAt == null) continue;
    if (isRunActive(session.run)) continue; // resumed after being marked done — leave it
    if (now - session.completedAt >= SESSION_TTL_MS) resetScraperState(session.portal);
  }
}

/** Which slot a job from `GET /runs` belongs to, and under which endpoint. */
export function slotForScraper(
  scraper: Portal | typeof SWEEP_SCRAPER,
): { portal: Portal; kind: SessionKind } {
  if (scraper === SWEEP_SCRAPER) return { portal: "myflorida", kind: "sweep" };
  return { portal: scraper, kind: "portal" };
}

// -- React bindings ----------------------------------------------------------

/**
 * One portal's slot, re-rendering only when that slot changes.
 *
 * `getServerSnapshot` returns undefined so the prerendered HTML holds no session
 * — the registry is browser state, and pretending otherwise would hydrate a
 * running scrape into a static page.
 */
export function useSession(portal: Portal): Session | undefined {
  return useSyncExternalStore(
    subscribe,
    useCallback(() => slots[portal], [portal]),
    () => undefined,
  );
}

/** Every slot, for the engine and anything counting what is in flight. */
export function useSlots(): Slots {
  return useSyncExternalStore(subscribe, getSlots, () => EMPTY_SLOTS);
}

const EMPTY_SLOTS: Slots = {};

/**
 * The portals holding a live run right now, as a stable key string.
 *
 * A string rather than an array so `useSyncExternalStore` has something it can
 * compare by value — returning a fresh array each call would loop forever.
 */
export function useActivePortals(): Set<Portal> {
  const key = useSyncExternalStore(subscribe, activePortalKey, () => "");
  return new Set(key ? (key.split(",") as Portal[]) : []);
}

function activePortalKey(): string {
  return Object.values(slots)
    .filter((s): s is Session => !!s?.run && isRunActive(s.run))
    .map((s) => s.portal)
    .sort()
    .join(",");
}

/** Every slot holding a run, live or finished — the engine's work list. */
export function sessionsWithRuns(): Session[] {
  return Object.values(slots).filter((s): s is Session => !!s && !!s.runId);
}

// -- the jobs feed -----------------------------------------------------------

/**
 * `GET /runs?active=true` — every run in flight on every portal, plus the pool's
 * capacity. One poll answers for the whole console, so the engine owns it and
 * publishes the result here rather than each consumer opening its own.
 */
export interface JobsFeed {
  jobs: Job[];
  capacity: JobCapacity | null;
  /** False until the first response lands, so consumers can tell "none" from
   *  "not asked yet" — the footer bar hides itself on the former, not both. */
  loaded: boolean;
}

let feed: JobsFeed = { jobs: [], capacity: null, loaded: false };
const feedListeners = new Set<() => void>();

function subscribeFeed(listener: () => void) {
  feedListeners.add(listener);
  return () => {
    feedListeners.delete(listener);
  };
}

export function putJobsFeed(jobs: Job[], capacity: JobCapacity | null) {
  feed = { jobs, capacity, loaded: true };
  for (const l of feedListeners) l();
}

function getFeed(): JobsFeed {
  return feed;
}

const EMPTY_FEED: JobsFeed = { jobs: [], capacity: null, loaded: false };

export function useJobsFeed(): JobsFeed {
  return useSyncExternalStore(subscribeFeed, getFeed, () => EMPTY_FEED);
}
