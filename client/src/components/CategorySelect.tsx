"use client";

import type { Category } from "@/lib/api";

const PRIORITY_OPTIONS = [
  { value: "high", label: "High only" },
  { value: "high_medium", label: "High + Medium" },
  { value: "all", label: "All codes" },
];

const PRIORITY_BADGE: Record<string, string> = {
  high: "bg-emerald-100 text-emerald-800",
  medium: "bg-amber-100 text-amber-800",
  related: "bg-slate-200 text-slate-600",
};

interface Props {
  categories: Category[];
  selected: string;
  priority: string;
  disabled?: boolean;
  onSelect: (key: string) => void;
  onPriorityChange: (priority: string) => void;
}

export default function CategorySelect({ categories, selected, priority, disabled, onSelect, onPriorityChange }: Props) {
  const allowed =
    priority === "high" ? ["high"] : priority === "high_medium" ? ["high", "medium"] : ["high", "medium", "related"];
  const current = categories.find((c) => c.key === selected);
  const activeCodes = current?.codes.filter((c) => allowed.includes(c.priority)) ?? [];

  return (
    <div className="space-y-4">
      <div>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">Category</h2>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {categories.map((category) => (
            <button
              key={category.key}
              type="button"
              disabled={disabled}
              onClick={() => onSelect(category.key)}
              className={`rounded-lg border p-3 text-left text-sm transition disabled:opacity-50 ${
                selected === category.key
                  ? "border-blue-600 bg-blue-50 font-semibold text-blue-900"
                  : "border-slate-200 bg-white text-slate-700 hover:border-blue-300"
              }`}
            >
              {category.label}
              <span className="mt-1 block text-xs font-normal text-slate-400">{category.codes.length} codes</span>
            </button>
          ))}
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">Code priority</h2>
        <div className="flex gap-2">
          {PRIORITY_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              disabled={disabled}
              onClick={() => onPriorityChange(option.value)}
              className={`rounded-full border px-3 py-1 text-xs transition disabled:opacity-50 ${
                priority === option.value
                  ? "border-blue-600 bg-blue-600 text-white"
                  : "border-slate-300 bg-white text-slate-600 hover:border-blue-400"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {current && (
        <div>
          <h2 className="mb-2 text-sm font-semibold text-slate-700">
            Codes to search ({activeCodes.length})
          </h2>
          <div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto rounded-lg border border-slate-200 bg-white p-3">
            {activeCodes.map((code) => (
              <span
                key={code.code}
                title={code.title}
                className={`rounded px-2 py-0.5 font-mono text-xs ${PRIORITY_BADGE[code.priority]}`}
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
