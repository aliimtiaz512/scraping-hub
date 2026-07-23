"use client";

import type { BidResult } from "@/lib/api";
import { DataTable } from "@/components/ui";

export default function UnisonResults({ bids }: { bids: BidResult[] }) {
  if (bids.length === 0) return null;

  return (
    <DataTable
      caption={`Results · ${bids.length} ${bids.length === 1 ? "request" : "requests"}`}
      headers={[
        { label: "Buyer #" },
        { label: "Description" },
        { label: "Buyer" },
        { label: "End Date" },
      ]}
    >
      {bids.map((bid, i) => (
        <tr key={`${bid.buyer_number ?? "row"}-${i}`} className="transition hover:bg-ink-50">
          <td className="whitespace-nowrap px-4 py-3 font-mono text-xs font-medium text-ink-900">
            {bid.buyer_number ?? "—"}
          </td>
          <td className="max-w-md truncate px-4 py-3 text-ink-700" title={bid.buyer_description ?? ""}>
            {bid.buyer_description || "—"}
          </td>
          <td className="max-w-[14rem] truncate px-4 py-3 text-ink-600" title={bid.buyer ?? ""}>
            {bid.buyer || "—"}
          </td>
          <td className="tabular whitespace-nowrap px-4 py-3 text-xs text-ink-600">{bid.end_date || "—"}</td>
        </tr>
      ))}
    </DataTable>
  );
}
