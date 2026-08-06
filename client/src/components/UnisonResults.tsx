"use client";

import type { BidResult } from "@/lib/api";
import { DataTable } from "@/components/ui";

/** Colour the evaluator's verdict so a long list scans at a glance. Matches the
 *  SAM results table — both portals run the same funnel, so a reader should not
 *  have to learn two vocabularies. */
function decisionTone(decision: string): string {
  switch (decision.toUpperCase()) {
    case "PURSUE":
      return "border-emerald-200 bg-emerald-50 text-emerald-700";
    case "REJECT":
      return "border-rose-200 bg-rose-50 text-rose-700";
    case "MANUAL_REVIEW":
      return "border-amber-200 bg-amber-50 text-amber-700";
    default:
      return "border-ink-200 bg-ink-50 text-ink-600"; // not evaluated / unknown
  }
}

function decisionLabel(decision: string): string {
  return decision.toUpperCase() === "MANUAL_REVIEW" ? "MANUAL REVIEW" : decision.toUpperCase();
}

export default function UnisonResults({ bids }: { bids: BidResult[] }) {
  if (bids.length === 0) return null;

  return (
    <DataTable
      caption={`Results · ${bids.length} ${bids.length === 1 ? "buy" : "buys"}`}
      headers={[
        { label: "Decision" },
        { label: "Buy #" },
        { label: "Description" },
        { label: "Buyer" },
        { label: "End Date" },
        { label: "Docs", className: "text-right" },
      ]}
    >
      {bids.map((bid, i) => {
        const decision = bid.decision ?? "—";
        return (
          <tr key={`${bid.buyer_number ?? "row"}-${i}`} className="align-top transition hover:bg-ink-50">
            <td className="px-4 py-3">
              {/* The reason is the tooltip: the standard phrase explains the
                  verdict without putting the evaluator's internals on screen. */}
              <span
                className={`inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-semibold ${decisionTone(decision)}`}
                title={bid.reason ?? ""}
              >
                {decisionLabel(decision)}
              </span>
            </td>
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
            <td className="tabular px-4 py-3 text-right text-xs text-ink-600">
              {bid.documents?.length ?? 0}
            </td>
          </tr>
        );
      })}
    </DataTable>
  );
}
