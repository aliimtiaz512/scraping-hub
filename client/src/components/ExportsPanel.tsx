"use client";

import { useEffect, useState } from "react";

import { Button, DataTable, EmptyState, ErrorBanner, LinkButton, Spinner } from "@/components/ui";
import { bidnetExportUrl } from "@/lib/api";
import { basename, fetchRunsState, formatTimestamp, runExcelPath, runTarget, RUNS_LOADING, type RunsState } from "@/lib/runs";
import type { PortalMeta } from "@/lib/portals";

/**
 * The spreadsheets and document folders past runs produced.
 *
 * Only BidNet exposes a download endpoint (`GET /bidnet/export`), so it is the
 * only portal offered as a real download. For the others the files live on the
 * server's disk and the honest affordance is the path itself — a fake download
 * button would just 404.
 */
export default function ExportsPanel({ meta }: { meta: PortalMeta }) {
  const [{ runs, error, loading }, setState] = useState<RunsState>(RUNS_LOADING);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchRunsState(meta.key, "exports").then((next) => {
      if (!cancelled) setState(next);
    });
    return () => {
      cancelled = true;
    };
  }, [meta.key]);

  const refresh = () => {
    setState((s) => ({ ...s, loading: true }));
    fetchRunsState(meta.key, "exports").then(setState);
  };

  const copyPath = async (path: string) => {
    try {
      await navigator.clipboard.writeText(path);
      setCopied(path);
      setTimeout(() => setCopied((c) => (c === path ? null : c)), 2000);
    } catch {
      setState((s) => ({ ...s, error: "Could not copy to clipboard — your browser blocked the request." }));
    }
  };

  const exportable = (runs ?? []).filter((run) => runExcelPath(run) !== null);

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="font-display text-2xl text-ink-900">Exports</h2>
          <p className="mt-1 text-sm text-ink-500">Spreadsheets and document folders produced by {meta.label} runs.</p>
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

      {meta.key === "bidnet" && (
        <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-ink-200 bg-white p-5 shadow-sm">
          <div className="min-w-0">
            <h3 className="font-display text-base text-ink-900">Full solicitation export</h3>
            <p className="mt-1 text-xs text-ink-500">
              Builds a fresh Excel of every BidNet solicitation stored in the database, across all runs.
            </p>
          </div>
          <LinkButton
            href={bidnetExportUrl()}
            variant="primary"
            icon={
              <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
                <path d="M8 2v8m0 0L5 7m3 3l3-3M2.5 12.5h11" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            }
          >
            Download Excel
          </LinkButton>
        </div>
      )}

      {runs === null && loading && (
        <div className="flex items-center justify-center gap-2 rounded-xl border border-ink-200 bg-white py-14 text-sm text-ink-500">
          <Spinner />
          Loading exports…
        </div>
      )}

      {runs !== null && exportable.length === 0 && !error && (
        <EmptyState
          icon={
            <svg viewBox="0 0 20 20" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
              <path d="M3.5 6a2 2 0 0 1 2-2h2.2l1.3 1.5h5.5a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2V6Z" />
            </svg>
          }
          title="No exports yet"
          description={`Runs that finish successfully write a spreadsheet. Start a ${meta.label} scrape from the Console to produce one.`}
        />
      )}

      {runs !== null && exportable.length > 0 && (
        <DataTable
          caption={`${exportable.length} ${exportable.length === 1 ? "export" : "exports"}`}
          headers={[
            { label: "Produced" },
            { label: "Searched for" },
            { label: "Bids", className: "text-right" },
            { label: "File" },
            { label: "", className: "text-right" },
          ]}
        >
          {exportable.map((run) => {
            const path = runExcelPath(run)!;
            return (
              <tr key={run.run_id} className="transition hover:bg-ink-50">
                <td className="tabular whitespace-nowrap px-4 py-3 text-xs text-ink-600">
                  {formatTimestamp(run.finished_at ?? run.started_at)}
                </td>
                <td className="max-w-xs truncate px-4 py-3 text-sm text-ink-700" title={runTarget(run)}>
                  {runTarget(run)}
                </td>
                <td className="tabular px-4 py-3 text-right text-sm text-ink-700">{run.bids_found}</td>
                <td className="max-w-sm truncate px-4 py-3 font-mono text-xs text-ink-500" title={path}>
                  {basename(path)}
                </td>
                <td className="px-4 py-3 text-right">
                  <Button size="sm" onClick={() => copyPath(path)}>
                    {copied === path ? "Copied" : "Copy path"}
                  </Button>
                </td>
              </tr>
            );
          })}
        </DataTable>
      )}
    </div>
  );
}
