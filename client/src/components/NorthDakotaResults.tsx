"use client";

import type { BidResult } from "@/lib/api";
import { DataTable } from "@/components/ui";

/** Colour the open/closed distinction so a long list scans quickly. */
function statusTone(status: string): string {
  const s = status.toLowerCase();
  if (s.includes("open") || s.includes("bidding") || s.includes("active")) {
    return "border-emerald-200 bg-emerald-50 text-emerald-700";
  }
  return "border-ink-200 bg-ink-50 text-ink-600";
}

export default function NorthDakotaResults({ bids }: { bids: BidResult[] }) {
  if (bids.length === 0) return null;

  return (
    <DataTable
      caption={`Results · ${bids.length} ${bids.length === 1 ? "solicitation" : "solicitations"}`}
      headers={[
        { label: "RFP ID" },
        { label: "RFx Name" },
        { label: "Commodities" },
        { label: "Publication" },
        { label: "Bid Due" },
        { label: "Status" },
      ]}
    >
      {bids.map((bid, i) => (
        <tr key={`${bid.rfp_id ?? "row"}-${i}`} className="transition hover:bg-ink-50">
          <td className="whitespace-nowrap px-4 py-3 font-mono text-xs font-medium text-ink-900">{bid.rfp_id ?? "—"}</td>
          <td className="max-w-xs truncate px-4 py-3 text-ink-700" title={bid.title ?? ""}>
            {bid.detail_url ? (
              <a href={bid.detail_url} target="_blank" rel="noreferrer" className="hover:text-rose-600 hover:underline">
                {bid.title ?? "—"}
              </a>
            ) : (
              (bid.title ?? "—")
            )}
          </td>
          <td className="max-w-[12rem] truncate px-4 py-3 text-xs text-ink-600" title={bid.commodity ?? ""}>
            {bid.commodity ?? "—"}
          </td>
          <td className="tabular whitespace-nowrap px-4 py-3 text-xs text-ink-600">{bid.pub_begin_date ?? "—"}</td>
          <td className="tabular whitespace-nowrap px-4 py-3 text-xs text-ink-600">{bid.close_date ?? "—"}</td>
          <td className="px-4 py-3">
            {bid.status ? (
              <span
                className={`inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${statusTone(bid.status)}`}
              >
                {bid.status}
              </span>
            ) : (
              <span className="text-ink-400">—</span>
            )}
          </td>
        </tr>
      ))}
    </DataTable>
  );
}
