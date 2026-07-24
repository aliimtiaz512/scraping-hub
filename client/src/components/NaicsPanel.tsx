"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button, Card, DataTable, EmptyState, ErrorBanner, MiniButton } from "@/components/ui";
import { getNaicsCodes, getRunStatus, startNaicsScrape, type NaicsResult } from "@/lib/api";

const PAGE_SIZE = 50;
const POLL_INTERVAL_MS = 2000;

export default function NaicsPanel() {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [results, setResults] = useState<NaicsResult[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshNote, setRefreshNote] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (q: string, p: number) => {
    setLoading(true);
    setError(null);
    try {
      const data = await getNaicsCodes(q.trim(), p, PAGE_SIZE);
      setResults(data.results);
      setTotal(data.total);
    } catch (e) {
      setError((e as Error).message);
      setResults([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, []);

  // Load whenever the query or page changes (debounced on the query).
  useEffect(() => {
    const t = setTimeout(() => load(query, page), 250);
    return () => clearTimeout(t);
  }, [query, page, load]);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const handleRefresh = async () => {
    setRefreshing(true);
    setRefreshNote("Refreshing the NAICS catalogue from the source…");
    setError(null);
    try {
      const { run_id } = await startNaicsScrape();
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const status = await getRunStatus("naics", run_id);
          if (status.status === "completed") {
            if (pollRef.current) clearInterval(pollRef.current);
            setRefreshing(false);
            setRefreshNote(`Refreshed — ${status.bids_found} codes in the catalogue.`);
            load(query, 1);
            setPage(1);
          } else if (status.status === "failed") {
            if (pollRef.current) clearInterval(pollRef.current);
            setRefreshing(false);
            setRefreshNote(null);
            setError(status.errors?.[0] ?? "Refresh failed.");
          }
        } catch {
          // transient — keep polling
        }
      }, POLL_INTERVAL_MS);
    } catch (e) {
      setRefreshing(false);
      setRefreshNote(null);
      setError((e as Error).message);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Card
        title="Search NAICS codes"
        description="Look up any 6-digit NAICS industry code or title. Use these when setting NAICS filters on the bid portals."
        actions={
          <MiniButton onClick={handleRefresh} disabled={refreshing}>
            {refreshing ? "Refreshing…" : "Refresh catalogue"}
          </MiniButton>
        }
      >
        <input
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(1);
          }}
          placeholder="e.g. 541511 or “software”"
          className="w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm transition placeholder:text-ink-400 focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/25"
        />
        {refreshNote && <p className="mt-2 text-xs text-ink-500">{refreshNote}</p>}
      </Card>

      {total === 0 && !loading ? (
        <EmptyState
          title={query.trim() ? "No matching codes" : "No codes loaded yet"}
          description={
            query.trim()
              ? "Try a different code or keyword."
              : "Click “Refresh catalogue” to pull the full NAICS list from the source."
          }
        />
      ) : (
        <div className="space-y-3">
          <DataTable
            caption={`${total.toLocaleString()} ${total === 1 ? "code" : "codes"}${query.trim() ? " matched" : ""}`}
            headers={[{ label: "Code" }, { label: "Title" }]}
          >
            {results.map((r) => (
              <tr key={r.code} className="transition hover:bg-ink-50">
                <td className="whitespace-nowrap px-4 py-3 font-mono text-xs font-medium text-ink-900">{r.code}</td>
                <td className="px-4 py-3 text-ink-700">{r.title}</td>
              </tr>
            ))}
          </DataTable>

          <div className="flex items-center justify-between px-1">
            <span className="text-xs text-ink-500">
              Page {page} of {totalPages}
            </span>
            <div className="flex gap-2">
              <Button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1 || loading}>
                Previous
              </Button>
              <Button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page >= totalPages || loading}>
                Next
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
