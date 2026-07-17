"use client";

import type { BidResult } from "@/lib/api";
import { DataTable, DocStatus } from "@/components/ui";

export default function BidnetResults({ bids }: { bids: BidResult[] }) {
  if (bids.length === 0) return null;

  return (
    <DataTable
      caption={`Results · ${bids.length} ${bids.length === 1 ? "solicitation" : "solicitations"}`}
      headers={[
        { label: "Reference #" },
        { label: "Title" },
        { label: "Matched keyword" },
        { label: "Type" },
        { label: "Closing" },
        { label: "Documents", className: "text-center" },
        { label: "Status" },
      ]}
    >
      {bids.map((bid, i) => (
        <tr key={bid.reference_number ?? i} className="transition hover:bg-ink-50">
          <td className="whitespace-nowrap px-4 py-3 font-mono text-xs font-medium text-ink-900">
            {bid.reference_number ?? "—"}
          </td>
          <td className="max-w-xs truncate px-4 py-3 text-ink-700" title={bid.title ?? ""}>
            {bid.title ?? "—"}
          </td>
          <td className="px-4 py-3">
            {bid.matched_keyword ? (
              <span className="whitespace-nowrap rounded-full border border-gold-200 bg-gold-50 px-2 py-0.5 text-xs font-medium text-gold-700">
                {bid.matched_keyword}
              </span>
            ) : (
              <span className="text-ink-400">—</span>
            )}
          </td>
          <td className="whitespace-nowrap px-4 py-3 text-xs text-ink-600">{bid.solicitation_type ?? "—"}</td>
          <td className="tabular whitespace-nowrap px-4 py-3 text-xs text-ink-600">{bid.closing_date ?? "—"}</td>
          <td className="tabular px-4 py-3 text-center text-ink-600">{bid.documents.length}</td>
          <td className="px-4 py-3">
            {bid.error ? (
              <DocStatus state="failed" title={bid.error} />
            ) : bid.documents.length ? (
              <DocStatus state="ok" />
            ) : (
              <DocStatus state="empty" />
            )}
          </td>
        </tr>
      ))}
    </DataTable>
  );
}
