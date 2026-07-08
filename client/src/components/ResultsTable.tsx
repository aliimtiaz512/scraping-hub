"use client";

import type { BidResult } from "@/lib/api";
import { DataTable, DocStatus } from "@/components/ui";

export default function ResultsTable({ bids }: { bids: BidResult[] }) {
  if (bids.length === 0) return null;

  return (
    <DataTable
      headers={[
        { label: "Number" },
        { label: "Title" },
        { label: "Docs", className: "text-center" },
        { label: "Status" },
      ]}
    >
      {bids.map((bid) => (
        <tr key={bid.number} className="border-b border-white/5 transition last:border-0 hover:bg-white/[0.03]">
          <td className="px-4 py-2.5 font-mono text-xs text-emerald-300">{bid.number}</td>
          <td className="max-w-md truncate px-4 py-2.5 text-slate-300" title={bid.title}>
            {bid.title}
          </td>
          <td className="px-4 py-2.5 text-center font-mono text-slate-400">{bid.documents.length}</td>
          <td className="px-4 py-2.5">
            {bid.error ? (
              <DocStatus state="failed" title={bid.error} />
            ) : bid.document_errors?.length ? (
              <DocStatus state="partial" />
            ) : (
              <DocStatus state="ok" />
            )}
          </td>
        </tr>
      ))}
    </DataTable>
  );
}
