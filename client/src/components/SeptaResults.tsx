"use client";

import type { BidResult, RunStatus } from "@/lib/api";
import { DataTable } from "@/components/ui";

export default function SeptaResults({ bids, run }: { bids: BidResult[]; run?: RunStatus }) {
  if (bids.length === 0) return null;

  const minDays = run?.min_days_until_close;
  const skipped = run?.bids_skipped_closing_soon ?? 0;
  const unreadable = run?.bids_kept_unreadable_close ?? 0;
  // Explain the (deliberately) smaller list: only quotes closing ≥ N days out
  // are kept, so the caption notes how many were dropped for closing too soon
  // and how many were kept despite an unreadable close date.
  const notes: string[] = [];
  if (minDays) notes.push(`closing ≥ ${minDays} days out`);
  if (skipped > 0) notes.push(`${skipped} closing sooner skipped`);
  if (unreadable > 0) notes.push(`${unreadable} with an unreadable close date kept`);
  const caption =
    `Results · ${bids.length} ${bids.length === 1 ? "quote" : "quotes"}` +
    (notes.length ? ` · ${notes.join(" · ")}` : "");

  return (
    <DataTable
      caption={caption}
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
