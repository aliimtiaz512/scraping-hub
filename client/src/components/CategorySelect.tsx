"use client";

import type { AdStatus, Category } from "@/lib/api";
import { SectionLabel } from "@/components/ui";

const PRIORITY_OPTIONS = [
  { value: "high", label: "High only" },
  { value: "high_medium", label: "High + Medium" },
  { value: "all", label: "All codes" },
];

// Multi-select; an empty selection leaves the portal's Ad Status list untouched
// (returns every status).
const AD_STATUS_OPTIONS: { value: AdStatus; label: string }[] = [
  { value: "preview", label: "Preview" },
  { value: "open", label: "Open" },
  { value: "closed", label: "Closed" },
  { value: "withdrawn", label: "Withdrawn" },
];

const PRIORITY_BADGE: Record<string, string> = {
  high: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
  medium: "border-amber-400/30 bg-amber-400/10 text-amber-300",
  related: "border-slate-500/30 bg-slate-500/10 text-slate-400",
};

interface Props {
  categories: Category[];
  selected: string;
  priority: string;
  adStatuses: AdStatus[];
  disabled?: boolean;
  onSelect: (key: string) => void;
  onPriorityChange: (priority: string) => void;
  onAdStatusChange: (adStatuses: AdStatus[]) => void;
}

export default function CategorySelect({
  categories,
  selected,
  priority,
  adStatuses,
  disabled,
  onSelect,
  onPriorityChange,
  onAdStatusChange,
}: Props) {
  const toggleAdStatus = (value: AdStatus) =>
    onAdStatusChange(
      adStatuses.includes(value) ? adStatuses.filter((s) => s !== value) : [...adStatuses, value],
    );
  const allowed =
    priority === "high" ? ["high"] : priority === "high_medium" ? ["high", "medium"] : ["high", "medium", "related"];
  const current = categories.find((c) => c.key === selected);
  const activeCodes = current?.codes.filter((c) => allowed.includes(c.priority)) ?? [];

  return (
    <div className="space-y-5">
      <div>
        <SectionLabel>Category</SectionLabel>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {categories.map((category) => {
            const isActive = selected === category.key;
            return (
              <button
                key={category.key}
                type="button"
                disabled={disabled}
                onClick={() => onSelect(category.key)}
                className={`rounded-xl border p-3 text-left text-sm transition disabled:opacity-50 ${
                  isActive
                    ? "border-emerald-400/40 bg-emerald-400/[0.08] font-semibold text-white shadow-[0_0_20px_-8px_rgba(16,185,129,0.8)]"
                    : "border-white/10 bg-white/[0.02] text-slate-300 hover:border-white/20 hover:bg-white/[0.04]"
                }`}
              >
                {category.label}
                <span className="mt-1 block font-mono text-[11px] font-normal text-slate-500">
                  {category.codes.length} codes
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <div>
        <SectionLabel>Code priority</SectionLabel>
        <div className="flex flex-wrap gap-2">
          {PRIORITY_OPTIONS.map((option) => {
            const isActive = priority === option.value;
            return (
              <button
                key={option.value}
                type="button"
                disabled={disabled}
                onClick={() => onPriorityChange(option.value)}
                className={`rounded-full border px-3 py-1 text-xs transition disabled:opacity-50 ${
                  isActive
                    ? "border-emerald-400/40 bg-emerald-400/15 text-emerald-200"
                    : "border-white/10 bg-white/[0.02] text-slate-400 hover:border-white/20 hover:text-slate-200"
                }`}
              >
                {option.label}
              </button>
            );
          })}
        </div>
      </div>

      <div>
        <SectionLabel>Ad status {adStatuses.length === 0 && <span className="normal-case text-slate-600">— any</span>}</SectionLabel>
        <div className="flex flex-wrap gap-2">
          {AD_STATUS_OPTIONS.map((option) => {
            const isActive = adStatuses.includes(option.value);
            return (
              <button
                key={option.value}
                type="button"
                disabled={disabled}
                onClick={() => toggleAdStatus(option.value)}
                className={`rounded-full border px-3 py-1 text-xs transition disabled:opacity-50 ${
                  isActive
                    ? "border-emerald-400/40 bg-emerald-400/15 text-emerald-200"
                    : "border-white/10 bg-white/[0.02] text-slate-400 hover:border-white/20 hover:text-slate-200"
                }`}
              >
                {option.label}
              </button>
            );
          })}
        </div>
      </div>

      {current && (
        <div>
          <SectionLabel>Codes to search ({activeCodes.length})</SectionLabel>
          <div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto rounded-xl border border-white/10 bg-slate-950/40 p-3">
            {activeCodes.map((code) => (
              <span
                key={code.code}
                title={code.title}
                className={`rounded-md border px-2 py-0.5 font-mono text-[11px] ${PRIORITY_BADGE[code.priority]}`}
              >
                {code.code}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
