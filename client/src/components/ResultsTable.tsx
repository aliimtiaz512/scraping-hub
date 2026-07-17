"use client";

import type { BidResult } from "@/lib/api";
import { DataTable, DocStatus } from "@/components/ui";

export default function ResultsTable({ bids }: { bids: BidResult[] }) {
  if (bids.length === 0) return null;

  return (
    <DataTable
      caption={`Results · ${bids.length} ${bids.length === 1 ? "bid" : "bids"}`}
      headers={[
        { label: "Number" },
        { label: "Title" },
        { label: "Documents", className: "text-center" },
        { label: "Status" },
      ]}
    >
      {bids.map((bid) => (
        <tr key={bid.number} className="transition hover:bg-ink-50">
          <td className="whitespace-nowrap px-4 py-3 font-mono text-xs font-medium text-ink-900">{bid.number}</td>
          <td className="max-w-md truncate px-4 py-3 text-ink-700" title={bid.title}>
            {bid.title}
          </td>
          <td className="tabular px-4 py-3 text-center text-ink-600">{bid.documents.length}</td>
          <td className="px-4 py-3">
            {bid.error ? (
              <DocStatus state="failed" title={bid.error} />
            ) : bid.document_errors?.length ? (
              <DocStatus state="partial" title={bid.document_errors.join("\n")} />
            ) : (
              <DocStatus state="ok" />
            )}
          </td>
        </tr>
      ))}
    </DataTable>
  );
}
