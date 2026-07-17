"use client";

import { useEffect, useState } from "react";

import PortalResults from "@/components/PortalResults";
import RunStatusPanel from "@/components/RunStatus";
import { Button, DataTable, EmptyState, ErrorBanner, IconButton, RunBadge, Spinner } from "@/components/ui";
import type { RunStatus } from "@/lib/api";
import { fetchRunsState, formatDuration, formatTimestamp, runTarget, RUNS_LOADING, type RunsState } from "@/lib/runs";
import type { PortalMeta } from "@/lib/portals";

/**
 * Every run the backend still holds for this portal. The run list is already
 * exposed by `GET /{portal}/scrape/runs`; this surfaces it.
 */
export default function RunHistory({ meta }: { meta: PortalMeta }) {
  const [{ runs, error, loading }, setState] = useState<RunsState>(RUNS_LOADING);
  const [selected, setSelected] = useState<RunStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchRunsState(meta.key, "run history").then((next) => {
      if (!cancelled) setState(next);
    });
    return () => {
      cancelled = true;
    };
  }, [meta.key]);

  const refresh = () => {
    setState((s) => ({ ...s, loading: true }));
    fetchRunsState(meta.key, "run history").then(setState);
  };

  if (selected) {
    return (
      <div className="space-y-5">
        <div className="flex items-center gap-3">
          <IconButton label="Back to run history" variant="secondary" onClick={() => setSelected(null)}>
            <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
              <path d="M10 3.5L5.5 8l4.5 4.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </IconButton>
          <div className="min-w-0">
            <h2 className="font-display text-xl text-ink-900">Run detail</h2>
            <p className="truncate font-mono text-xs text-ink-500">{selected.run_id}</p>
          </div>
        </div>
        <RunStatusPanel run={selected} />
        <PortalResults portal={meta.key} bids={selected.bids ?? []} />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="font-display text-2xl text-ink-900">Run history</h2>
          <p className="mt-1 text-sm text-ink-500">
            Every {meta.label} run currently held by the server. Select a row to inspect it.
          </p>
        </div>
        <Button
          size="sm"
          onClick={refresh}
          loading={loading}
          icon={
            <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
              <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2v3h-3" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          }
        >
          Refresh
        </Button>
      </div>

      {error && <ErrorBanner message={error} />}

      {runs === null && loading && (
        <div className="flex items-center justify-center gap-2 rounded-xl border border-ink-200 bg-white py-14 text-sm text-ink-500">
          <Spinner />
          Loading run history…
        </div>
      )}

      {runs !== null && runs.length === 0 && !error && (
        <EmptyState
          icon={
            <svg viewBox="0 0 20 20" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
              <circle cx="10" cy="10" r="7" />
              <path d="M10 6v4l2.5 2" strokeLinecap="round" />
            </svg>
          }
          title="No runs yet"
          description={`Once you start a ${meta.label} scrape from the Console, every run will be listed here.`}
        />
      )}

      {runs !== null && runs.length > 0 && (
        <DataTable
          caption={`${runs.length} ${runs.length === 1 ? "run" : "runs"}`}
          headers={[
            { label: "Status" },
            { label: "Started" },
            { label: "Duration" },
            { label: "Searched for" },
            { label: "Bids", className: "text-right" },
            { label: "Docs", className: "text-right" },
            { label: "" },
          ]}
        >
          {runs.map((run) => (
            <tr
              key={run.run_id}
              onClick={() => setSelected(run)}
              className="cursor-pointer transition hover:bg-ink-50"
            >
              <td className="px-4 py-3">
                <RunBadge status={run.status} />
              </td>
              <td className="tabular whitespace-nowrap px-4 py-3 text-xs text-ink-600">
                {formatTimestamp(run.started_at)}
              </td>
              <td className="tabular whitespace-nowrap px-4 py-3 text-xs text-ink-600">{formatDuration(run)}</td>
              <td className="max-w-xs truncate px-4 py-3 text-sm text-ink-700" title={runTarget(run)}>
                {runTarget(run)}
              </td>
              <td className="tabular px-4 py-3 text-right text-sm text-ink-700">{run.bids_found}</td>
              <td className="tabular px-4 py-3 text-right text-sm text-ink-700">{run.documents_downloaded}</td>
              <td className="px-4 py-3 text-right">
                {run.errors.length > 0 && (
                  <span
                    className="whitespace-nowrap rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-xs font-medium text-red-700"
                    title={run.errors.join("\n")}
                  >
                    {run.errors.length} {run.errors.length === 1 ? "error" : "errors"}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </DataTable>
      )}
    </div>
  );
}
