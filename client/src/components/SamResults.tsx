"use client";

import type { BidResult } from "@/lib/api";
import { DataTable } from "@/components/ui";

/** Colour the evaluator's verdict so a long list scans at a glance. */
function decisionTone(decision: string): string {
  switch (decision.toUpperCase()) {
    case "PURSUE":
      return "border-emerald-200 bg-emerald-50 text-emerald-700";
    case "REJECT":
      return "border-rose-200 bg-rose-50 text-rose-700";
    default:
      return "border-ink-200 bg-ink-50 text-ink-600"; // PENDING / ERROR / unknown
  }
}

function decisionLabel(decision: string): string {
  return decision.toUpperCase();
}

export default function SamResults({ bids }: { bids: BidResult[] }) {
  if (bids.length === 0) return null;

  return (
    <DataTable
      caption={`Results · ${bids.length} ${bids.length === 1 ? "bid" : "bids"}`}
      headers={[
        { label: "Decision" },
        { label: "Notice Title" },
        { label: "Notice ID" },
        { label: "NAICS" },
        { label: "Department" },
        { label: "Offers Due" },
      ]}
    >
      {bids.map((bid, i) => {
        const decision = bid.decision ?? "PENDING";
        return (
          <tr key={`${bid.notice_id ?? "row"}-${i}`} className="align-top transition hover:bg-ink-50">
            <td className="px-4 py-3">
              <span
                className={`inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-semibold ${decisionTone(decision)}`}
                title={bid.reason ?? ""}
              >
                {decisionLabel(decision)}
              </span>
            </td>
            <td className="max-w-sm px-4 py-3 text-ink-700">
              <div className="truncate" title={bid.title ?? ""}>
                {bid.title || "—"}
              </div>
              {bid.reason && <div className="mt-0.5 truncate text-xs text-ink-400" title={bid.reason}>{bid.reason}</div>}
            </td>
            <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-ink-600">{bid.notice_id ?? "—"}</td>
            <td className="whitespace-nowrap px-4 py-3 text-xs text-ink-600">
              {bid.naics_code || "—"}
              {bid.naics_title && <div className="max-w-[10rem] truncate text-ink-400" title={bid.naics_title}>{bid.naics_title}</div>}
            </td>
            <td className="max-w-[12rem] truncate px-4 py-3 text-xs text-ink-600" title={bid.department ?? ""}>
              {bid.department || "—"}
            </td>
            <td className="tabular whitespace-nowrap px-4 py-3 text-xs text-ink-600">{bid.date_offers_due || "—"}</td>
          </tr>
        );
      })}
    </DataTable>
  );
}
