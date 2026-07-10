"use client";

import type { BidResult } from "@/lib/api";
import { DataTable } from "@/components/ui";

export default function WisconsinResults({ bids }: { bids: BidResult[] }) {
  if (bids.length === 0) return null;

  return (
    <DataTable
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
        <tr key={`${bid.event_number ?? "row"}-${i}`} className="border-b border-white/5 transition last:border-0 hover:bg-white/[0.03]">
          <td className="px-4 py-2.5 font-mono text-xs text-emerald-300">{bid.event_number ?? "—"}</td>
          <td className="px-4 py-2.5 font-mono text-xs text-slate-400">{bid.solicitation_reference ?? "—"}</td>
          <td className="px-4 py-2.5 text-xs text-slate-400">{bid.event_type ?? "—"}</td>
          <td className="max-w-xs truncate px-4 py-2.5 text-slate-300" title={bid.event_title ?? ""}>
            {bid.event_title ?? "—"}
          </td>
          <td className="max-w-[10rem] truncate px-4 py-2.5 text-xs text-slate-400" title={bid.agency ?? ""}>
            {bid.agency ?? "—"}
          </td>
          <td className="px-4 py-2.5 text-xs text-slate-400">{bid.event_status ?? "—"}</td>
          <td className="px-4 py-2.5 font-mono text-xs text-slate-400">{bid.due_datetime ?? "—"}</td>
        </tr>
      ))}
    </DataTable>
  );
}
