"use client";

import type { BidResult } from "@/lib/api";
import { DataTable } from "@/components/ui";

export default function SeptaResults({ bids }: { bids: BidResult[] }) {
  if (bids.length === 0) return null;

  return (
    <DataTable
      caption={`Results · ${bids.length} ${bids.length === 1 ? "quote" : "quotes"}`}
      headers={[
        { label: "Requisition #" },
        { label: "Summary" },
        { label: "Open Date" },
        { label: "Close Date" },
      ]}
    >
      {bids.map((bid, i) => (
        <tr key={`${bid.requisition_number ?? "row"}-${i}`} className="transition hover:bg-ink-50">
          <td className="whitespace-nowrap px-4 py-3 font-mono text-xs font-medium text-ink-900">
            {bid.requisition_number ?? "—"}
          </td>
          <td className="max-w-md truncate px-4 py-3 text-ink-700" title={bid.summary ?? ""}>
            {bid.summary || "—"}
          </td>
          <td className="tabular whitespace-nowrap px-4 py-3 text-xs text-ink-600">{bid.open_date || "—"}</td>
          <td className="tabular whitespace-nowrap px-4 py-3 text-xs text-ink-600">{bid.close_date || "—"}</td>
        </tr>
      ))}
    </DataTable>
  );
}
