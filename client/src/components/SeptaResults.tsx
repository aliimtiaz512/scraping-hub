"use client";

import type { BidResult, SeptaModule } from "@/lib/api";
import { DataTable } from "@/components/ui";

/**
 * SEPTA results, in the shape of whichever module the run searched.
 *
 * The two grids key on different columns — Open Quotes on a requisition number
 * and a summary, Open Bids on a bid number and a title — so the table follows
 * the run's `module` rather than trying to render one set of headers over both.
 * A run only ever produces one module's rows, so there is nothing to merge.
 */
export default function SeptaResults({
  bids,
  module = "quotes",
}: {
  bids: BidResult[];
  module?: SeptaModule;
}) {
  if (bids.length === 0) return null;

  const openBids = module === "open_bids";
  const noun = openBids ? "bid" : "quote";

  return (
    <DataTable
      caption={`Results · ${bids.length} ${bids.length === 1 ? noun : `${noun}s`}`}
      headers={[
        { label: openBids ? "Bid #" : "Requisition #" },
        { label: openBids ? "Bid Title" : "Summary" },
        { label: "Open Date" },
        { label: "Close Date" },
      ]}
    >
      {bids.map((bid, i) => {
        const key = openBids ? bid.bid_number : bid.requisition_number;
        const text = openBids ? bid.title : bid.summary;
        return (
          <tr key={`${key ?? "row"}-${i}`} className="transition hover:bg-ink-50">
            <td className="whitespace-nowrap px-4 py-3 font-mono text-xs font-medium text-ink-900">
              {key ?? "—"}
            </td>
            <td className="max-w-md truncate px-4 py-3 text-ink-700" title={text ?? ""}>
              {text || "—"}
            </td>
            <td className="tabular whitespace-nowrap px-4 py-3 text-xs text-ink-600">{bid.open_date || "—"}</td>
            <td className="tabular whitespace-nowrap px-4 py-3 text-xs text-ink-600">{bid.close_date || "—"}</td>
          </tr>
        );
      })}
    </DataTable>
  );
}
