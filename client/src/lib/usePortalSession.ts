"use client";

import { useCallback } from "react";

import type { Portal, RunStatus } from "@/lib/api";
import {
  attachRun,
  beginLaunch,
  failLaunch,
  patchRunStatus,
  resetScraperState,
  setSessionError,
  useSession,
  type SessionKind,
} from "@/lib/sessions";

/**
 * A portal panel's view of its slot.
 *
 * This is the whole of a panel's execution state, and none of it is local: the
 * run, its streaming log tail, the launch error and the launching flag all live
 * in the session registry, so the panel renders the same thing whether it has
 * been mounted for an hour or for a millisecond. Mounting reads; it never
 * starts, stops or resets anything.
 *
 * Panels keep their *form* state local, as they always did — which portal
 * options are selected is the page's business, not the run's.
 */
export function usePortalSession(portal: Portal) {
  const session = useSession(portal);

  /**
   * Launch a scrape and hand the run to the engine.
   *
   * `start` is the portal's own endpoint call, so each panel keeps its own
   * arguments; all this adds is the bookkeeping every panel was repeating —
   * clear the error, flag the launch, register the run id, and let the engine
   * take it from there. No interval is created: the panel is not what watches
   * the run.
   */
  const launch = useCallback(
    async (
      start: () => Promise<{ run_id: string }>,
      { kind = "portal" as SessionKind } = {},
    ): Promise<string | null> => {
      beginLaunch(portal);
      try {
        const { run_id } = await start();
        // Registered before the first status arrives, so the panel switches to
        // the running view on the click rather than on the next engine tick.
        attachRun(portal, run_id, kind);
        return run_id;
      } catch (e) {
        failLaunch(portal, (e as Error).message);
        return null;
      }
    },
    [portal],
  );

  const setError = useCallback(
    (message: string | null) => setSessionError(portal, message),
    [portal],
  );

  /** Apply a status this console just caused (pause/resume) without waiting. */
  const applyStatus = useCallback(
    (status: RunStatus["status"]) => patchRunStatus(portal, status),
    [portal],
  );

  /** The panel's Refresh button: wipe this slot's logs, run and error, back to
   *  a never-launched state. Does not touch the server — see `resetScraperState`
   *  for why an in-flight run must be stopped by the caller first. */
  const reset = useCallback(() => resetScraperState(portal), [portal]);

  return {
    run: session?.run ?? null,
    logs: session?.logs ?? EMPTY_LOGS,
    error: session?.error ?? null,
    starting: session?.starting ?? false,
    runId: session?.runId ?? "",
    // Whether there is anything for Refresh to clear — a slot that has never
    // been launched has no runId, and resetting it would be a no-op.
    hasSession: !!session?.runId,
    launch,
    setError,
    applyStatus,
    reset,
  };
}

const EMPTY_LOGS: never[] = [];
