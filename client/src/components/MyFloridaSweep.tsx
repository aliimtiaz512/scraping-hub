"use client";

import { useEffect, useState } from "react";

import { Card, Chip } from "@/components/ui";
import {
  getSweepNiches,
  type AdStatusOption,
  type SweepNichesResponse,
} from "@/lib/api";

const STATUS_LABELS: Record<AdStatusOption, string> = {
  preview: "Preview",
  open: "Open",
  closed: "Closed",
  withdrawn: "Withdrawn",
};

const ALL_STATUSES: AdStatusOption[] = ["preview", "open", "closed", "withdrawn"];

interface Props {
  adStatuses: AdStatusOption[];
  maxBids: number | null;
  disabled?: boolean;
  onStatusChange: (next: AdStatusOption[]) => void;
  onMaxBidsChange: (next: number | null) => void;
}

/**
 * The sweep's whole configuration surface: which ad statuses to search, and an
 * optional ceiling for trial runs. Deliberately spare — the sweep sets exactly
 * one portal filter, which is what distinguishes it from the niche flow.
 */
export default function MyFloridaSweep({
  adStatuses,
  maxBids,
  disabled,
  onStatusChange,
  onMaxBidsChange,
}: Props) {
  const [catalogue, setCatalogue] = useState<SweepNichesResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    getSweepNiches()
      .then(setCatalogue)
      .catch((e: Error) => setLoadError(e.message));
  }, []);

  const toggle = (status: AdStatusOption) =>
    onStatusChange(
      adStatuses.includes(status)
        ? adStatuses.filter((s) => s !== status)
        : [...adStatuses, status],
    );

  return (
    <div className="space-y-5">
      <Card
        title="Ad status"
        description="The sweep's only portal filter. Every advertisement returned is classified — nothing is filtered out by the evaluator."
      >
        <div className="flex flex-wrap gap-2">
          {ALL_STATUSES.map((status) => (
            <Chip
              key={status}
              active={adStatuses.includes(status)}
              disabled={disabled}
              onClick={() => toggle(status)}
            >
              {STATUS_LABELS[status]}
            </Chip>
          ))}
        </div>
        {adStatuses.length === 0 && (
          <p className="mt-2 text-xs text-red-600">
            Pick at least one status — a sweep with no filter is not supported.
          </p>
        )}
      </Card>

      <Card
        title="Bid cap"
        description="Optional ceiling for a trial run. A full sweep opens and reads every advertisement it finds, which can take hours."
      >
        <div className="flex items-center gap-3">
          <input
            type="number"
            min={1}
            value={maxBids ?? ""}
            disabled={disabled}
            onChange={(e) => {
              const value = e.target.value.trim();
              onMaxBidsChange(value === "" ? null : Math.max(1, Number(value)));
            }}
            placeholder="No limit"
            className="w-40 rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm transition placeholder:text-ink-400 focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/25 disabled:cursor-not-allowed disabled:bg-ink-50"
          />
          <span className="text-xs text-ink-500">
            {maxBids ? `Stops after ${maxBids} advertisements.` : "Every advertisement found."}
          </span>
        </div>
      </Card>

      <Card
        title="Classification lanes"
        description="One sheet per niche in the delivered workbook, plus Other for anything below the match threshold."
        actions={
          catalogue ? (
            <span className="tabular text-xs text-ink-500">
              threshold {String(catalogue.thresholds?.niche_match ?? "—")}
            </span>
          ) : undefined
        }
      >
        {loadError && (
          <p className="text-xs text-red-600">
            Could not load the niche catalogue — is the API running? ({loadError})
          </p>
        )}
        {catalogue && (
          <>
            <div className="space-y-1.5">
              {catalogue.niches.map((niche) => (
                <div
                  key={niche.key}
                  className="flex items-baseline justify-between gap-3 rounded-lg border border-ink-200 bg-white px-3 py-2"
                >
                  <div className="min-w-0">
                    <span className="font-mono text-xs font-medium text-ink-900">{niche.key}</span>
                    <span className="ml-2 text-sm text-ink-700">{niche.label}</span>
                  </div>
                  <span className="shrink-0 text-xs text-ink-500">
                    {niche.core_terms} terms · {niche.tier_a_codes} tier-A codes
                  </span>
                </div>
              ))}
              <div className="flex items-baseline justify-between gap-3 rounded-lg border border-dashed border-ink-200 bg-ink-50/60 px-3 py-2">
                <span className="text-sm text-ink-600">{catalogue.other_sheet}</span>
                <span className="text-xs text-ink-500">everything below the threshold</span>
              </div>
            </div>
            <p className="mt-2.5 text-xs text-ink-500">
              Criteria v{catalogue.version} ·{" "}
              {catalogue.cross_listing
                ? "cross-listing on — a bid can appear in a secondary lane too"
                : "one lane per bid"}
            </p>
          </>
        )}
      </Card>
    </div>
  );
}
