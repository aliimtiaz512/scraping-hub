"use client";

import { useState } from "react";

import type { BidnetKeyword, BidnetNiche } from "@/lib/api";
import { Card, MiniButton } from "@/components/ui";

interface Props {
  niches: BidnetNiche[];
  selected: string[];
  disabled?: boolean;
  onChange: (selected: string[]) => void;
}

type Tier = "core" | "extended";

/** The folder a niche+tier selection produces, e.g. Bidnetdirect_AI-ML_core. */
function folderName(slug: string, tier: Tier): string {
  return `Bidnetdirect_${slug}_${tier}`;
}

export default function KeywordSelect({ niches, selected, disabled, onChange }: Props) {
  const [custom, setCustom] = useState("");
  const selectedSet = new Set(selected);

  const toggle = (term: string) =>
    onChange(selectedSet.has(term) ? selected.filter((t) => t !== term) : [...selected, term]);

  const addTerms = (terms: string[]) =>
    onChange([...selected, ...terms.filter((t) => !selectedSet.has(t))]);

  const clearTerms = (terms: string[]) => {
    const drop = new Set(terms);
    onChange(selected.filter((t) => !drop.has(t)));
  };

  const addCustom = () => {
    const term = custom.trim();
    if (term && !selectedSet.has(term)) onChange([...selected, term]);
    setCustom("");
  };

  // Terms that belong to no catalog niche → shown under a Custom group.
  const catalogTerms = new Set(
    niches.flatMap((n) => [...n.core, ...n.extended]).map((k) => k.term),
  );
  const customSelected = selected.filter((t) => !catalogTerms.has(t));

  return (
    <div className="space-y-5">
      {niches.map((niche) => {
        const coreTerms = niche.core.map((k) => k.term);
        const extendedTerms = niche.extended.map((k) => k.term);
        const chosen = [...coreTerms, ...extendedTerms].filter((t) => selectedSet.has(t)).length;
        const producing = (["core", "extended"] as Tier[]).filter((tier) =>
          (tier === "core" ? coreTerms : extendedTerms).some((t) => selectedSet.has(t)),
        );

        return (
          <Card
            key={niche.key}
            title={niche.label}
            description="Core (Tier 1) terms are the highest-yield searches; extended terms are specialized."
            actions={<span className="tabular text-xs text-ink-500">{chosen} selected</span>}
          >
            <div className="space-y-4">
              <TierSection
                label="Core"
                hint="Tier 1"
                keywords={niche.core}
                selectedSet={selectedSet}
                disabled={disabled}
                onToggle={toggle}
                onSelectAll={() => addTerms(coreTerms)}
                onClear={() => clearTerms(coreTerms)}
              />
              <TierSection
                label="Extended"
                hint="Tier 2"
                keywords={niche.extended}
                selectedSet={selectedSet}
                disabled={disabled}
                onToggle={toggle}
                onSelectAll={() => addTerms(extendedTerms)}
                onClear={() => clearTerms(extendedTerms)}
              />

              {producing.length > 0 && (
                <p className="text-xs text-ink-500">
                  → produces:{" "}
                  {producing.map((tier, i) => (
                    <span key={tier}>
                      {i > 0 && ", "}
                      <span className="font-mono text-ink-600">{folderName(niche.slug, tier)}</span>
                    </span>
                  ))}
                </p>
              )}
            </div>
          </Card>
        );
      })}

      <Card title="Custom keyword" description="Search a term that isn't in the catalog above.">
        <div className="flex gap-2">
          <input
            type="text"
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addCustom();
              }
            }}
            placeholder="e.g. fleet maintenance services"
            disabled={disabled}
            className="w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm transition placeholder:text-ink-400 focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/20 disabled:cursor-not-allowed disabled:bg-ink-50"
          />
          <button
            type="button"
            onClick={addCustom}
            disabled={disabled || custom.trim() === ""}
            className="shrink-0 rounded-lg border border-ink-200 bg-white px-3.5 py-2 text-sm font-medium text-ink-700 shadow-sm transition hover:bg-ink-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Add
          </button>
        </div>
        <p className="mt-2 text-xs text-ink-500">
          Custom terms run in their own <span className="font-mono">Bidnetdirect_Custom</span> folder.
        </p>
      </Card>

      <SelectedSummary
        niches={niches}
        selected={selected}
        customSelected={customSelected}
        selectedSet={selectedSet}
        disabled={disabled}
        onRemove={(term) => onChange(selected.filter((t) => t !== term))}
        onClearAll={() => onChange([])}
      />
    </div>
  );
}

/** One tier's pills plus its bulk select/clear controls. */
function TierSection({
  label,
  hint,
  keywords,
  selectedSet,
  disabled,
  onToggle,
  onSelectAll,
  onClear,
}: {
  label: string;
  hint: string;
  keywords: BidnetKeyword[];
  selectedSet: Set<string>;
  disabled?: boolean;
  onToggle: (term: string) => void;
  onSelectAll: () => void;
  onClear: () => void;
}) {
  const isCore = label === "Core";
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-ink-700">{label}</span>
          <span className="rounded bg-ink-100 px-1.5 py-px text-[10px] font-semibold text-ink-500">{hint}</span>
        </div>
        <div className="flex gap-1.5">
          <MiniButton disabled={disabled} onClick={onSelectAll}>
            Select {label.toLowerCase()}
          </MiniButton>
          <MiniButton disabled={disabled} onClick={onClear}>
            Clear
          </MiniButton>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        {keywords.map((kw) => {
          const isActive = selectedSet.has(kw.term);
          return (
            <button
              key={kw.term}
              type="button"
              title={kw.notes}
              disabled={disabled}
              onClick={() => onToggle(kw.term)}
              aria-pressed={isActive}
              className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition disabled:cursor-not-allowed disabled:opacity-50 ${
                isActive
                  ? "border-gold-200 bg-gold-50 font-medium text-gold-700 hover:bg-gold-100"
                  : isCore
                    ? "border-ink-300 bg-white font-medium text-ink-800 hover:border-ink-400 hover:bg-ink-50"
                    : "border-ink-200 bg-white text-ink-600 hover:border-ink-300 hover:bg-ink-50 hover:text-ink-900"
              }`}
            >
              {kw.term}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** The running selection, grouped by niche + tier (and a Custom group). */
function SelectedSummary({
  niches,
  selected,
  customSelected,
  selectedSet,
  disabled,
  onRemove,
  onClearAll,
}: {
  niches: BidnetNiche[];
  selected: string[];
  customSelected: string[];
  selectedSet: Set<string>;
  disabled?: boolean;
  onRemove: (term: string) => void;
  onClearAll: () => void;
}) {
  const rows: { label: string; terms: string[] }[] = [];
  for (const niche of niches) {
    for (const [tier, list] of [
      ["core", niche.core],
      ["extended", niche.extended],
    ] as const) {
      const terms = list.map((k) => k.term).filter((t) => selectedSet.has(t));
      if (terms.length) rows.push({ label: `${niche.label} · ${tier}`, terms });
    }
  }
  if (customSelected.length) rows.push({ label: "Custom", terms: customSelected });

  return (
    <Card
      title="Selected keywords"
      description={
        selected.length === 0
          ? "Nothing selected yet — pick at least one keyword to run a search."
          : `${selected.length} ${selected.length === 1 ? "search" : "searches"} will run, one per keyword, foldered by niche + tier.`
      }
      actions={
        selected.length > 0 ? (
          <MiniButton disabled={disabled} onClick={onClearAll}>
            Clear all
          </MiniButton>
        ) : undefined
      }
    >
      {selected.length === 0 ? (
        <div className="rounded-lg border border-dashed border-ink-200 bg-ink-50/60 px-4 py-6 text-center">
          <p className="text-sm text-ink-500">Selected keywords will appear here, grouped by niche and tier.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {rows.map((row) => (
            <div key={row.label}>
              <p className="mb-1.5 text-xs font-semibold text-ink-600">{row.label}</p>
              <div className="flex flex-wrap gap-2">
                {row.terms.map((term) => (
                  <span
                    key={term}
                    className="group inline-flex items-center gap-1.5 rounded-full border border-gold-200 bg-gold-50 py-1 pl-3 pr-1.5 text-xs font-medium text-gold-700"
                  >
                    {term}
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => onRemove(term)}
                      aria-label={`Remove ${term}`}
                      className="flex h-4 w-4 items-center justify-center rounded-full text-gold-500 transition hover:bg-gold-200 hover:text-gold-700 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <svg viewBox="0 0 10 10" className="h-2.5 w-2.5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
                        <path d="M1.5 1.5l7 7M8.5 1.5l-7 7" strokeLinecap="round" />
                      </svg>
                    </button>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
