"use client";

import { useState } from "react";

import type { KeywordGroup } from "@/lib/api";
import { Card, MiniButton } from "@/components/ui";

interface Props {
  groups: KeywordGroup[];
  selected: string[];
  disabled?: boolean;
  onChange: (selected: string[]) => void;
}

export default function KeywordSelect({ groups, selected, disabled, onChange }: Props) {
  const [custom, setCustom] = useState("");
  const selectedSet = new Set(selected);

  const toggle = (term: string) =>
    onChange(selectedSet.has(term) ? selected.filter((t) => t !== term) : [...selected, term]);

  const remove = (term: string) => onChange(selected.filter((t) => t !== term));

  const addAllTier1 = (group: KeywordGroup) => {
    const tier1 = group.keywords.filter((k) => k.tier === "tier1").map((k) => k.term);
    onChange([...selected, ...tier1.filter((t) => !selectedSet.has(t))]);
  };

  const clearGroup = (group: KeywordGroup) => {
    const terms = new Set(group.keywords.map((k) => k.term));
    onChange(selected.filter((t) => !terms.has(t)));
  };

  const addCustom = () => {
    const term = custom.trim();
    if (term && !selectedSet.has(term)) onChange([...selected, term]);
    setCustom("");
  };

  return (
    <div className="space-y-5">
      {groups.map((group) => {
        const chosen = group.keywords.filter((k) => selectedSet.has(k.term)).length;
        return (
          <Card
            key={group.key}
            title={group.label}
            description="Tier 1 terms are the highest-yield searches for this group."
            actions={
              <>
                <span className="tabular mr-1 text-xs text-ink-500">{chosen} selected</span>
                <MiniButton disabled={disabled} onClick={() => addAllTier1(group)}>
                  Add Tier 1
                </MiniButton>
                <MiniButton disabled={disabled} onClick={() => clearGroup(group)}>
                  Clear
                </MiniButton>
              </>
            }
          >
            <div className="flex flex-wrap gap-2">
              {group.keywords.map((kw) => {
                const isActive = selectedSet.has(kw.term);
                const isTier1 = kw.tier === "tier1";
                return (
                  <button
                    key={kw.term}
                    type="button"
                    title={kw.notes}
                    disabled={disabled}
                    onClick={() => toggle(kw.term)}
                    aria-pressed={isActive}
                    className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition disabled:cursor-not-allowed disabled:opacity-50 ${
                      isActive
                        ? "border-gold-200 bg-gold-50 font-medium text-gold-700 hover:bg-gold-100"
                        : isTier1
                          ? "border-ink-300 bg-white font-medium text-ink-800 hover:border-ink-400 hover:bg-ink-50"
                          : "border-ink-200 bg-white text-ink-600 hover:border-ink-300 hover:bg-ink-50 hover:text-ink-900"
                    }`}
                  >
                    {kw.term}
                    {isTier1 && (
                      <span
                        className={`rounded px-1 py-px text-[10px] font-semibold leading-none ${
                          isActive ? "bg-gold-200/70 text-gold-700" : "bg-ink-100 text-ink-500"
                        }`}
                      >
                        T1
                      </span>
                    )}
                  </button>
                );
              })}
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
        <p className="mt-2 text-xs text-ink-500">Each keyword is searched separately for the best results.</p>
      </Card>

      <Card
        title="Selected keywords"
        description={
          selected.length === 0
            ? "Nothing selected yet — pick at least one keyword to run a search."
            : `${selected.length} ${selected.length === 1 ? "search" : "searches"} will run, one per keyword.`
        }
        actions={
          selected.length > 0 ? (
            <MiniButton disabled={disabled} onClick={() => onChange([])}>
              Clear all
            </MiniButton>
          ) : undefined
        }
      >
        {selected.length === 0 ? (
          <div className="rounded-lg border border-dashed border-ink-200 bg-ink-50/60 px-4 py-6 text-center">
            <p className="text-sm text-ink-500">Selected keywords will appear here.</p>
          </div>
        ) : (
          <div className="flex max-h-44 flex-wrap gap-2 overflow-y-auto">
            {selected.map((term) => (
              <span
                key={term}
                className="group inline-flex items-center gap-1.5 rounded-full border border-gold-200 bg-gold-50 py-1 pl-3 pr-1.5 text-xs font-medium text-gold-700"
              >
                {term}
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => remove(term)}
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
        )}
      </Card>
    </div>
  );
}
