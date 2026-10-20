"use client";

import { useEffect, useState } from "react";
import { listPpraBids, type PpraBid } from "@/lib/api";

export default function PpraResults({
  runId,
  initialBids = [],
}: {
  runId?: string;
  initialBids?: PpraBid[];
}) {
  const [bids, setBids] = useState<PpraBid[]>(initialBids);
  const [filterQuery, setFilterQuery] = useState("");

  useEffect(() => {
    if (!runId) return;

    let active = true;
    let lastId = 0;

    const poll = async () => {
      try {
        const res = await listPpraBids({ run_id: runId, after_id: lastId, limit: 100 });
        if (!active) return;
        if (res.bids && res.bids.length > 0) {
          setBids((prev) => {
            const existingIds = new Set(prev.map((b) => b.id));
            const newBids = res.bids.filter((b) => !existingIds.has(b.id));
            return [...newBids, ...prev];
          });
          lastId = Math.max(...res.bids.map((b) => b.id));
        }
      } catch {
        // Silently tolerate transient polling error
      }
    };

    poll();
    const timer = setInterval(poll, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [runId]);

  const displayed = bids.filter((b) => {
    if (!filterQuery) return true;
    const q = filterQuery.toLowerCase();
    return (
      (b.title || "").toLowerCase().includes(q) ||
      (b.tender_no || "").toLowerCase().includes(q) ||
      (b.organization || "").toLowerCase().includes(q) ||
      (b.city || "").toLowerCase().includes(q)
    );
  });

  return (
    <div className="space-y-4 rounded-xl border border-ink-200/70 bg-white p-6 shadow-sm shadow-ink-900/[0.03]">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="font-display text-xl text-ink-900">
            Live tender results ({bids.length})
          </h2>
          <p className="text-xs text-ink-500">
            Real-time feed of active federal tenders and corrigendums from PPRA.
          </p>
        </div>

        <input
          type="text"
          value={filterQuery}
          onChange={(e) => setFilterQuery(e.target.value)}
          placeholder="Filter live rows..."
          className="rounded-lg border border-ink-200 px-3 py-1.5 text-xs text-ink-800 focus:border-ink-400 focus:outline-none"
        />
      </div>

      <div className="overflow-x-auto rounded-lg border border-ink-100">
        <table className="w-full text-left text-xs">
          <thead className="border-b border-ink-100 bg-ink-50/70 font-medium text-ink-600">
            <tr>
              <th className="px-3 py-2.5">Tender No</th>
              <th className="px-3 py-2.5">Title</th>
              <th className="px-3 py-2.5">Organization</th>
              <th className="px-3 py-2.5">Sector</th>
              <th className="px-3 py-2.5">Status</th>
              <th className="px-3 py-2.5">Closing (PKT)</th>
              <th className="px-3 py-2.5">Link</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100/70 text-ink-800">
            {displayed.length === 0 ? (
              <tr>
                <td colSpan={7} className="p-8 text-center text-ink-400">
                  No tenders collected yet. Start a scrape above to begin streaming rows.
                </td>
              </tr>
            ) : (
              displayed.map((b) => (
                <tr key={b.id || b.tender_no} className="hover:bg-ink-50/40">
                  <td className="whitespace-nowrap px-3 py-2.5 font-mono font-medium text-ink-900">
                    {b.tender_no}
                  </td>
                  <td className="max-w-xs truncate px-3 py-2.5" title={b.title}>
                    {b.title}
                  </td>
                  <td className="max-w-xs truncate px-3 py-2.5 text-ink-600" title={b.organization ?? undefined}>
                    {b.organization || "—"}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-ink-500">
                    {b.sector || "—"}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5">
                    <span
                      className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${
                        b.status?.toLowerCase().includes("corrigendum")
                          ? "bg-amber-50 text-amber-700 ring-1 ring-amber-200"
                          : b.status?.toLowerCase().includes("cancelled")
                          ? "bg-rose-50 text-rose-700 ring-1 ring-rose-200"
                          : "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200"
                      }`}
                    >
                      {b.status || "Published"}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 font-mono text-ink-600">
                    {b.closing_at || "—"}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5">
                    {b.detail_url ? (
                      <a
                        href={b.detail_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-emerald-600 underline hover:text-emerald-700"
                      >
                        View
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

