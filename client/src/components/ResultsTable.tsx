"use client";

import type { BidResult } from "@/lib/api";

export default function ResultsTable({ bids }: { bids: BidResult[] }) {
  if (bids.length === 0) return null;

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase text-slate-500">
            <th className="px-4 py-2">Number</th>
            <th className="px-4 py-2">Title</th>
            <th className="px-4 py-2 text-center">Documents</th>
            <th className="px-4 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {bids.map((bid) => (
            <tr key={bid.number} className="border-b border-slate-100 last:border-0">
              <td className="px-4 py-2 font-mono text-xs">{bid.number}</td>
              <td className="max-w-md truncate px-4 py-2" title={bid.title}>
                {bid.title}
              </td>
              <td className="px-4 py-2 text-center">{bid.documents.length}</td>
              <td className="px-4 py-2">
                {bid.error ? (
                  <span className="text-xs text-red-600" title={bid.error}>
                    failed
                  </span>
                ) : bid.document_errors?.length ? (
                  <span className="text-xs text-amber-600">partial</span>
                ) : (
                  <span className="text-xs text-emerald-600">ok</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
