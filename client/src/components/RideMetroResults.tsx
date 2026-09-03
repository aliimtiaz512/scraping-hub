"use client";

import type { BidResult, RideMetroAgency } from "@/lib/api";
import { DataTable } from "@/components/ui";

/**
 * RideMetro results: the Euna Supplier Network sweep.
 *
 * The roster is rendered above the table because it carries facts no bid row
 * can — an agency skipped for an Incomplete registration, and one that was
 * visited and simply had nothing open, look identical from the rows alone.
 * Opportunities are grouped under their agency, matching the Excel report.
 */
export default function RideMetroResults({
  bids,
  agencies = [],
}: {
  bids: BidResult[];
  agencies?: RideMetroAgency[];
}) {
  if (bids.length === 0 && agencies.length === 0) return null;

  return (
    <div className="space-y-4">
      {agencies.length > 0 && <Roster agencies={agencies} />}

      {bids.length > 0 && (
        <DataTable
          caption={`Results · ${bids.length} ${bids.length === 1 ? "opportunity" : "opportunities"}`}
          headers={[
            { label: "Agency" },
            { label: "Ref #" },
            { label: "Project" },
            { label: "Closing Date" },
            { label: "Days Left", className: "text-right" },
          ]}
        >
          {/* Index in the key, and `||` not `??`: an unread field is "" rather
              than null, which would collide on the key and blank the cell. */}
          {bids.map((bid, i) => (
            <tr key={`${bid.ref_number || "row"}-${i}`} className="transition hover:bg-ink-50">
              <td className="max-w-[14rem] truncate px-4 py-3 text-ink-700" title={bid.agency ?? ""}>
                {bid.agency || "—"}
              </td>
              <td className="whitespace-nowrap px-4 py-3 font-mono text-xs font-medium text-ink-900">
                {bid.ref_number || "—"}
              </td>
              <td className="max-w-md truncate px-4 py-3 text-ink-700" title={bid.project ?? ""}>
                {bid.project || "—"}
              </td>
              <td className="tabular whitespace-nowrap px-4 py-3 text-xs text-ink-600">
                {bid.close_date || "—"}
              </td>
              <td className="tabular whitespace-nowrap px-4 py-3 text-right text-xs text-ink-600">
                {bid.days_left || "—"}
              </td>
            </tr>
          ))}
        </DataTable>
      )}
    </div>
  );
}

function Roster({ agencies }: { agencies: RideMetroAgency[] }) {
  const scraped = agencies.filter((a) => !a.skipped && !a.error && !a.note).length;

  return (
    <section className="rounded-xl border border-ink-200 bg-white p-4 shadow-sm">
      <h4 className="mb-3 text-xs font-semibold uppercase tracking-wide text-ink-500">
        My Network · {scraped} of {agencies.length} agencies scraped
      </h4>
      <ul className="space-y-1.5">
        {agencies.map((agency) => (
          <li key={agency.url || agency.name} className="flex items-baseline justify-between gap-3 text-sm">
            <span className="min-w-0 truncate text-ink-800" title={agency.name}>
              {agency.name}
            </span>
            <span className="shrink-0 text-xs">
              {agency.error ? (
                <span className="text-red-600" title={agency.error}>
                  Failed
                </span>
              ) : agency.note ? (
                <span className="text-ink-400" title={agency.note}>
                  No portal
                </span>
              ) : agency.skipped ? (
                <span className="text-ink-400">Skipped · {agency.status || "Incomplete"}</span>
              ) : (
                <span className="tabular text-ink-600">
                  {agency.opportunities} {agency.opportunities === 1 ? "opportunity" : "opportunities"}
                </span>
              )}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
