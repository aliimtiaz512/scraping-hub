"use client";

import { useEffect, useState } from "react";

import { PORTALS } from "@/lib/portals";
import { fetchTotals, type Totals } from "@/lib/runs";

const PORTAL_KEYS = PORTALS.map((p) => p.key);

/**
 * Live proof strip under the hero. Renders nothing until the API answers, and
 * nothing at all if it never does — an empty gap beats fabricated numbers or a
 * row of dashes on a landing page.
 */
export default function LandingStats() {
  const [totals, setTotals] = useState<Totals | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchTotals(PORTAL_KEYS).then((next) => {
      if (!cancelled) setTotals(next);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!totals) return <div className="h-px" aria-hidden />;

  const items = [
    { value: totals.sourcesUp, label: totals.sourcesUp === 1 ? "portal monitored" : "portals monitored" },
    { value: totals.runs.toLocaleString(), label: totals.runs === 1 ? "run completed" : "runs completed" },
    { value: totals.bids.toLocaleString(), label: totals.bids === 1 ? "bid collected" : "bids collected" },
    { value: totals.documents.toLocaleString(), label: totals.documents === 1 ? "document saved" : "documents saved" },
  ];

  return (
    <dl className="flex flex-wrap items-center gap-x-10 gap-y-6 border-t border-ink-200/70 pt-8">
      {items.map((item) => (
        <div key={item.label}>
          <dt className="sr-only">{item.label}</dt>
          <dd>
            <span className="tabular font-display text-3xl text-ink-900">{item.value}</span>
            <span className="mt-1 block text-xs text-ink-500">{item.label}</span>
          </dd>
        </div>
      ))}
    </dl>
  );
}
