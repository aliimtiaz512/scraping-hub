"use client";

import type { BidResult } from "@/lib/api";
import { DataTable } from "@/components/ui";

/**
 * The ad-status sweep's live results.
 *
 * Separate from `ResultsTable` because the sweep produces different rows, not
 * differently-styled ones: it classifies every advertisement rather than
 * downloading each one's attachments, so there is no document count and no
 * per-bid download status to show. What it has instead is the classifier's
 * verdict — the niche it landed in, its score, and how firm that call is.
 *
 * The server sends a rolling window of the most recent rows (not the whole run),
 * so this is a progress view; the complete set lands in the delivered workbook.
 */
export default function MyFloridaSweepResults({ bids }: { bids: BidResult[] }) {
  if (bids.length === 0) return null;

  return (
    <DataTable
      caption={`Classified · latest ${bids.length} ${bids.length === 1 ? "ad" : "ads"}`}
      headers={[
        { label: "Number" },
        { label: "Title" },
        { label: "Niche" },
        { label: "Score", className: "text-center" },
        { label: "Match" },
      ]}
    >
      {bids.map((bid) => (
        <tr key={bid.number} className="transition hover:bg-ink-50">
          <td className="whitespace-nowrap px-4 py-3 font-mono text-xs font-medium text-ink-900">{bid.number}</td>
          <td className="max-w-md truncate px-4 py-3 text-ink-700" title={bid.title ?? ""}>
            {bid.title ?? "—"}
          </td>
          <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-ink-700">{bid.niche ?? "—"}</td>
          <td className="tabular px-4 py-3 text-center text-ink-600">{bid.score ?? 0}</td>
          <td className="px-4 py-3">
            <MatchStrength strength={bid.strength} />
          </td>
        </tr>
      ))}
    </DataTable>
  );
}

/**
 * STRONG / PROBABLE / POSSIBLE, or nothing at all — the classifier returns null
 * below the match threshold, which is exactly the case that lands an ad in
 * Other, so it reads as "no match" rather than as a missing value.
 */
function MatchStrength({ strength }: { strength?: string | null }) {
  const map: Record<string, string> = {
    STRONG: "border-emerald-200 bg-emerald-50 text-emerald-700",
    PROBABLE: "border-gold-300 bg-gold-50 text-gold-700",
    POSSIBLE: "border-ink-200 bg-ink-50 text-ink-600",
  };
  const cls = strength ? map[strength] : undefined;
  return (
    <span
      className={`inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${
        cls ?? "border-ink-200 bg-ink-50 text-ink-500"
      }`}
    >
      {strength ?? "No match"}
    </span>
  );
}
