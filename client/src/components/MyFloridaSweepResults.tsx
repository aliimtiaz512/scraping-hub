"use client";

import type { BidResult } from "@/lib/api";
import { DataTable } from "@/components/ui";

/**
 * The ad-status sweep's live results.
 *
 * Two shapes, because the sweep has had two. A current run *captures* every
 * advertisement — its attachments are kept in a folder of its own and nothing is
 * scored — so the row says what was collected and where it went. A run recorded
 * before that change carries the classifier's verdict instead: the niche it
 * landed in, its score, and how firm the call was. Which columns to show is
 * decided from the rows themselves, so a historical run still renders as it did.
 *
 * The server sends a rolling window of the most recent rows (not the whole run),
 * so this is a progress view; the complete set lands in the delivered archive.
 */
export default function MyFloridaSweepResults({ bids }: { bids: BidResult[] }) {
  if (bids.length === 0) return null;

  const classified = bids.some((bid) => bid.niche !== undefined && bid.niche !== null);
  if (!classified) {
    const documents = bids.reduce((sum, bid) => sum + (bid.document_count ?? 0), 0);
    return (
      <DataTable
        caption={
          `Captured · latest ${bids.length} ${bids.length === 1 ? "ad" : "ads"}` +
          ` · ${documents} document${documents === 1 ? "" : "s"} saved`
        }
        headers={[
          { label: "Number" },
          { label: "Title" },
          { label: "Documents", className: "text-center" },
          { label: "Folder in archive" },
        ]}
      >
        {bids.map((bid) => (
          <tr key={bid.number} className="transition hover:bg-ink-50">
            <td className="whitespace-nowrap px-4 py-3 font-mono text-xs font-medium text-ink-900">{bid.number}</td>
            <td className="max-w-md truncate px-4 py-3 text-ink-700" title={bid.title ?? ""}>
              {bid.title ?? "—"}
            </td>
            <td className="tabular px-4 py-3 text-center text-ink-600">{bid.document_count ?? 0}</td>
            <td className="max-w-xs truncate px-4 py-3 font-mono text-xs text-ink-500" title={bid.folder ?? ""}>
              {bid.folder ?? "—"}
            </td>
          </tr>
        ))}
      </DataTable>
    );
  }

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
