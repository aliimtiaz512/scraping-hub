"use client";

import type { BidResult } from "@/lib/api";

export default function RideMetroResults({ bids }: { bids: BidResult[] }) {
  if (bids.length === 0) return null;

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase text-slate-500">
            <th className="px-4 py-2">Ref. #</th>
            <th className="px-4 py-2">Project</th>
            <th className="px-4 py-2 text-center">Documents</th>
            <th className="px-4 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {bids.map((bid, i) => (
            <tr key={bid.ref_number ?? i} className="border-b border-slate-100 last:border-0">
              <td className="px-4 py-2 font-mono text-xs">{bid.ref_number ?? "—"}</td>
              <td className="max-w-md truncate px-4 py-2" title={bid.project ?? ""}>
                {bid.project ?? "—"}
              </td>
              <td className="px-4 py-2 text-center">{bid.documents.length}</td>
              <td className="px-4 py-2">
                {bid.error ? (
                  <span className="text-xs text-red-600" title={bid.error}>
                    failed
                  </span>
                ) : bid.documents.length ? (
                  <span className="text-xs text-emerald-600">ok</span>
                ) : (
                  <span className="text-xs text-amber-600">no zip</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
