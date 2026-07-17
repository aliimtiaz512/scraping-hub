"use client";

import type { BidResult } from "@/lib/api";
import { DataTable } from "@/components/ui";

/** Colour the live/closed distinction so a long list scans quickly. */
function statusTone(status: string): string {
  const s = status.toLowerCase();
  if (s.includes("open") || s.includes("active") || s.includes("posted")) {
    return "border-emerald-200 bg-emerald-50 text-emerald-700";
  }
  if (s.includes("closed") || s.includes("award") || s.includes("cancel")) {
    return "border-ink-200 bg-ink-50 text-ink-600";
  }
  return "border-ink-200 bg-ink-50 text-ink-600";
}

export default function WisconsinResults({ bids }: { bids: BidResult[] }) {
  if (bids.length === 0) return null;

  return (
    <DataTable
      caption={`Results · ${bids.length} ${bids.length === 1 ? "event" : "events"}`}
      headers={[
        { label: "Event #" },
        { label: "Reference #" },
        { label: "Type" },
        { label: "Title" },
        { label: "Agency" },
        { label: "Status" },
        { label: "Due" },
      ]}
    >
      {bids.map((bid, i) => (
        <tr key={`${bid.event_number ?? "row"}-${i}`} className="transition hover:bg-ink-50">
          <td className="whitespace-nowrap px-4 py-3 font-mono text-xs font-medium text-ink-900">{bid.event_number ?? "—"}</td>
          <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-ink-600">{bid.solicitation_reference ?? "—"}</td>
          <td className="whitespace-nowrap px-4 py-3 text-xs text-ink-600">{bid.event_type ?? "—"}</td>
          <td className="max-w-xs truncate px-4 py-3 text-ink-700" title={bid.event_title ?? ""}>
            {bid.event_title ?? "—"}
          </td>
          <td className="max-w-[10rem] truncate px-4 py-3 text-xs text-ink-600" title={bid.agency ?? ""}>
            {bid.agency ?? "—"}
          </td>
          <td className="px-4 py-3">
            {bid.event_status ? (
              <span
                className={`inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${statusTone(bid.event_status)}`}
              >
                {bid.event_status}
              </span>
            ) : (
              <span className="text-ink-400">—</span>
            )}
          </td>
          <td className="tabular whitespace-nowrap px-4 py-3 text-xs text-ink-600">{bid.due_datetime ?? "—"}</td>
        </tr>
      ))}
    </DataTable>
  );
}
