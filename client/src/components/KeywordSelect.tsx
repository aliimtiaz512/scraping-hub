"use client";

import { useState } from "react";

import type { KeywordGroup } from "@/lib/api";
import { SectionLabel } from "@/components/ui";

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
      {groups.map((group) => (
        <div key={group.key}>
          <div className="mb-2 flex items-center justify-between gap-3">
            <SectionLabel>{group.label}</SectionLabel>
            <div className="flex gap-1.5 text-[10px]">
              <button
                type="button"
                disabled={disabled}
                onClick={() => addAllTier1(group)}
                className="rounded-full border border-white/10 px-2 py-0.5 text-slate-400 transition hover:border-emerald-400/40 hover:text-emerald-200 disabled:opacity-50"
              >
                + all Tier 1
              </button>
              <button
                type="button"
                disabled={disabled}
                onClick={() => clearGroup(group)}
                className="rounded-full border border-white/10 px-2 py-0.5 text-slate-400 transition hover:border-white/20 hover:text-slate-200 disabled:opacity-50"
              >
                clear
              </button>
            </div>
          </div>
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
                  className={`rounded-full border px-3 py-1 text-xs transition disabled:opacity-50 ${
                    isActive
                      ? "border-emerald-400/40 bg-emerald-400/15 text-emerald-200"
                      : isTier1
                        ? "border-white/15 bg-white/[0.04] text-slate-200 hover:border-white/25"
                        : "border-white/10 bg-white/[0.02] text-slate-400 hover:border-white/20 hover:text-slate-200"
                  }`}
                >
                  {kw.term}
                  {isTier1 && !isActive && <span className="ml-1.5 text-[9px] text-emerald-500/70">T1</span>}
                </button>
              );
            })}
          </div>
        </div>
      ))}

      <div>
        <SectionLabel>Add custom keyword</SectionLabel>
        <div className="flex items-center gap-2 rounded-xl border border-white/10 bg-slate-950/50 px-3 py-2 transition focus-within:border-emerald-400/40 focus-within:ring-1 focus-within:ring-emerald-400/30">
          <span className="font-mono text-sm text-emerald-500">+</span>
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
            placeholder="Type a keyword and press Enter"
            disabled={disabled}
            className="w-full bg-transparent text-sm text-slate-100 outline-none placeholder:text-slate-600 disabled:opacity-50"
          />
        </div>
        <p className="mt-1.5 text-[11px] text-slate-500">Each keyword is searched separately for the best results.</p>
      </div>

      <div>
        <SectionLabel>
          Selected keywords ({selected.length})
          {selected.length === 0 && <span className="ml-1 normal-case tracking-normal text-slate-600">— none yet</span>}
        </SectionLabel>
        {selected.length > 0 && (
          <div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto rounded-xl border border-white/10 bg-slate-950/40 p-3">
            {selected.map((term) => (
              <button
                key={term}
                type="button"
                disabled={disabled}
                onClick={() => remove(term)}
                title="Remove"
                className="group flex items-center gap-1 rounded-md border border-emerald-400/30 bg-emerald-400/10 px-2 py-0.5 text-[11px] text-emerald-200 transition hover:border-red-400/40 hover:bg-red-400/10 hover:text-red-200 disabled:opacity-50"
              >
                {term}
                <span className="text-emerald-500/60 group-hover:text-red-300">×</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
