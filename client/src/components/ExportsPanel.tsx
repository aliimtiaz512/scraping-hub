"use client";

import { useEffect, useState } from "react";

import { Button, DataTable, EmptyState, ErrorBanner, LinkButton, Spinner } from "@/components/ui";
import { bidnetExportUrl, runDownloadUrl } from "@/lib/api";
import { fetchRunsState, formatTimestamp, runDownloadable, runTarget, RUNS_LOADING, type RunsState } from "@/lib/runs";
import type { PortalMeta } from "@/lib/portals";

/**
 * Per-run downloads for past runs — the fallback when the completion email
 * didn't arrive. Every row's button hits `GET /runs/{id}/download`, which
 * serves the run's archive ZIP: the cumulative Excel report plus all
 * downloaded bid documents in their niche-wise folders. Nothing sits in
 * data/documents — runs are packaged into the archive on completion.
 */
export default function ExportsPanel({ meta }: { meta: PortalMeta }) {
  const [{ runs, error, loading }, setState] = useState<RunsState>(RUNS_LOADING);

  useEffect(() => {
    let cancelled = false;
    fetchRunsState(meta.key, "downloads").then((next) => {
      if (!cancelled) setState(next);
    });
    return () => {
      cancelled = true;
    };
  }, [meta.key]);

  const refresh = () => {
    setState((s) => ({ ...s, loading: true }));
    fetchRunsState(meta.key, "downloads").then(setState);
  };

  const downloadable = (runs ?? []).filter(runDownloadable);

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="font-display text-2xl text-ink-900">Downloads</h2>
          <p className="mt-1 text-sm text-ink-500">
            Every completed {meta.label} run as one ZIP — the cumulative Excel report plus all downloaded bid documents.
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
          Loading downloads…
        </div>
      )}

      {runs !== null && downloadable.length === 0 && !error && (
        <EmptyState
          icon={
            <svg viewBox="0 0 20 20" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
              <path d="M10 3v9m0 0l-3.5-3.5M10 12l3.5-3.5M4 16.5h12" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          }
          title="Nothing to download yet"
          description={`Runs that finish successfully can be downloaded here. Start a ${meta.label} scrape from the Console to produce one.`}
        />
      )}

      {runs !== null && downloadable.length > 0 && (
        <DataTable
          caption={`${downloadable.length} ${downloadable.length === 1 ? "download" : "downloads"}`}
          headers={[
            { label: "Produced" },
            { label: "Searched for" },
            { label: "Bids", className: "text-right" },
            { label: "Docs", className: "text-right" },
            { label: "", className: "text-right" },
          ]}
        >
          {downloadable.map((run) => {
            return (
              <tr key={run.run_id} className="transition hover:bg-ink-50">
                <td className="tabular whitespace-nowrap px-4 py-3 text-xs text-ink-600">
                  {formatTimestamp(run.finished_at ?? run.started_at)}
                </td>
                <td className="max-w-xs truncate px-4 py-3 text-sm text-ink-700" title={runTarget(run)}>
                  {runTarget(run)}
                </td>
                <td className="tabular px-4 py-3 text-right text-sm text-ink-700">{run.bids_found}</td>
                <td className="tabular px-4 py-3 text-right text-sm text-ink-700">{run.documents_downloaded}</td>
                <td className="px-4 py-3 text-right">
                  <LinkButton
                    href={runDownloadUrl(run.run_id)}
                    size="sm"
                    icon={
                      <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
                        <path d="M8 2v8m0 0L5 7m3 3l3-3M2.5 12.5h11" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    }
                  >
                    Download ZIP
                  </LinkButton>
                </td>
              </tr>
            );
          })}
        </DataTable>
      )}
    </div>
  );
}
