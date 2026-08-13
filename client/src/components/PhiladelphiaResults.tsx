"use client";

import type { BidResult } from "@/lib/api";
import { DataTable } from "@/components/ui";

/**
 * The Open Bids a run has captured so far.
 *
 * The columns are the ones a reader acts on — what it is, who wants it, when it
 * opens — plus how many files came with it and the folder they are in, because
 * that folder name is how a row in the console maps to a directory in the
 * downloaded ZIP.
 */
export default function PhiladelphiaResults({ bids }: { bids: BidResult[] }) {
  if (bids.length === 0) return null;

  const documents = bids.reduce((sum, bid) => sum + (bid.document_count ?? 0), 0);

  return (
    <DataTable
      caption={
        `${bids.length} open bid${bids.length === 1 ? "" : "s"}` +
        ` · ${documents} document${documents === 1 ? "" : "s"} saved`
      }
      headers={[
        { label: "Bid #" },
        { label: "Description" },
        { label: "Buyer" },
        { label: "Bid Opening Date" },
        { label: "Documents", className: "text-center" },
        { label: "Folder in archive" },
      ]}
    >
      {bids.map((bid, index) => (
        // The bid number identifies a row, but nothing guarantees the portal
        // sends it — or sends it once — so the position carries the key. The
        // list only ever grows as a run progresses, so positions are stable.
        <tr key={`${bid.bid_number ?? bid.title ?? "bid"}-${index}`} className="transition hover:bg-ink-50">
          <td className="whitespace-nowrap px-4 py-3 font-mono text-xs font-medium text-ink-900">
            {bid.bid_number ?? "—"}
          </td>
          <td className="max-w-md truncate px-4 py-3 text-ink-700" title={bid.title ?? ""}>
            {bid.title ?? "—"}
          </td>
          <td className="whitespace-nowrap px-4 py-3 text-ink-600">{bid.buyer ?? "—"}</td>
          <td className="whitespace-nowrap px-4 py-3 text-ink-600">{bid.close_date ?? "—"}</td>
          <td className="tabular px-4 py-3 text-center text-ink-600">{bid.document_count ?? 0}</td>
          <td
            className="max-w-xs truncate px-4 py-3 font-mono text-xs text-ink-500"
            title={bid.error ?? bid.folder ?? ""}
          >
            {bid.error ? <span className="text-red-600">{bid.error}</span> : bid.folder ?? "—"}
          </td>
        </tr>
      ))}
    </DataTable>
  );
}
