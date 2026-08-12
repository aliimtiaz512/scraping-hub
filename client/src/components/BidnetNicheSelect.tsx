"use client";

import { Card } from "@/components/ui";
import type { BidnetNiche } from "@/lib/api";

/**
 * The only search input a BidNet run takes: which business sector to scrape.
 *
 * The keywords each niche owns live in the database and are resolved
 * server-side — they are deliberately not shown or sent here. The run searches
 * every one of them separately, one at a time, in a single session.
 */
export default function BidnetNicheSelect({
  niches,
  selected,
  disabled,
  onSelect,
}: {
  niches: BidnetNiche[];
  selected: string;
  disabled?: boolean;
  onSelect: (key: string) => void;
}) {
  const current = niches.find((niche) => niche.key === selected) ?? null;
  const empty = niches.length === 0;

  return (
    <Card
      title="Niche"
      description="Pick a sector — the scraper searches every keyword it owns, one search each, and merges the results into a single folder and spreadsheet."
    >
      <label className="mb-1.5 block text-xs font-semibold text-ink-700" htmlFor="bidnet-niche">
        Business sector
      </label>
      <select
        id="bidnet-niche"
        value={selected}
        disabled={disabled || empty}
        onChange={(event) => onSelect(event.target.value)}
        className="w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm transition focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/25 disabled:cursor-not-allowed disabled:bg-ink-50 disabled:text-ink-400"
      >
        {empty ? (
          <option value="">No niches configured</option>
        ) : (
          <>
            <option value="">Select a niche…</option>
            {niches.map((niche) => (
              <option key={niche.key} value={niche.key}>
                {niche.label} ({niche.search_count ?? niche.keyword_count} searches)
              </option>
            ))}
          </>
        )}
      </select>

      <p className="mt-2 text-xs leading-relaxed text-ink-500">
        {empty
          ? "Add niches to server/app/scrapers/bidnet/niches.py and restart the API."
          : current
            ? `${current.keyword_count} keyword searches${current.nigp_count ? ` and ${current.nigp_count} NIGP code searches` : ""} will run in sequence, in one browser session. A bid found by more than one term is collected once. Every bid and document lands in one folder with a single master spreadsheet.`
            : "Each niche maps to a set of procurement keywords and NIGP codes held on the server."}
      </p>
    </Card>
  );
}
