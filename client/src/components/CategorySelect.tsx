"use client";

import type { AdStatus, AdType, Category, SearchMode } from "@/lib/api";
import { SectionLabel } from "@/components/ui";

const SEARCH_MODE_OPTIONS: { value: SearchMode; label: string; hint: string }[] = [
  { value: "keywords", label: "Keyword scraping", hint: "one search per keyword" },
  { value: "codes", label: "Commodity code scraping", hint: "one search, all codes" },
];

// Multi-select; an empty selection leaves the portal's Ad Status list untouched
// (returns every status).
const AD_STATUS_OPTIONS: { value: AdStatus; label: string }[] = [
  { value: "preview", label: "Preview" },
  { value: "open", label: "Open" },
  { value: "closed", label: "Closed" },
  { value: "withdrawn", label: "Withdrawn" },
];

// Multi-select; an empty selection leaves the portal's Ad Type list untouched
// (returns every type). Order matches the portal's own list.
const AD_TYPE_OPTIONS: { value: AdType; label: string }[] = [
  { value: "agency_decision", label: "Agency Decision" },
  { value: "grant_opportunities", label: "Grant Opportunities" },
  { value: "informational_notice", label: "Informational Notice" },
  { value: "invitation_to_bid", label: "Invitation to Bid" },
  { value: "invitation_to_negotiate", label: "Invitation to Negotiate" },
  { value: "request_for_proposals", label: "Request for Proposals" },
  { value: "public_meeting_notice", label: "Public Meeting Notice" },
  { value: "request_for_information", label: "Request for Information" },
  { value: "request_for_statement_of_qualifications", label: "Request for Statement of Qualifications" },
  { value: "single_source", label: "Single Source" },
];

const CHIP_BASE = "rounded-full border px-3 py-1 text-xs transition disabled:opacity-50";
const CHIP_ON = "border-emerald-400/40 bg-emerald-400/15 text-emerald-200";
const CHIP_OFF = "border-white/10 bg-white/[0.02] text-slate-400 hover:border-white/20 hover:text-slate-200";

interface Props {
  categories: Category[];
  selected: string;
  mode: SearchMode;
  selectedCodes: string[];
  selectedKeywords: string[];
  adStatuses: AdStatus[];
  adTypes: AdType[];
  disabled?: boolean;
  onSelect: (key: string) => void;
  onModeChange: (mode: SearchMode) => void;
  onCodesChange: (codes: string[]) => void;
  onKeywordsChange: (keywords: string[]) => void;
  onAdStatusChange: (adStatuses: AdStatus[]) => void;
  onAdTypeChange: (adTypes: AdType[]) => void;
}

function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
}

export default function CategorySelect({
  categories,
  selected,
  mode,
  selectedCodes,
  selectedKeywords,
  adStatuses,
  adTypes,
  disabled,
  onSelect,
  onModeChange,
  onCodesChange,
  onKeywordsChange,
  onAdStatusChange,
  onAdTypeChange,
}: Props) {
  const current = categories.find((c) => c.key === selected);
  const allCodes = current?.codes ?? [];
  const allKeywords = current?.keywords ?? [];
  const searchingKeywords = mode === "keywords";

  return (
    <div className="space-y-5">
      <div>
        <SectionLabel>Niche</SectionLabel>
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
                  {category.keywords.length} keywords · {category.codes.length} codes
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <div>
        <SectionLabel>Search by</SectionLabel>
        <div className="flex flex-wrap gap-2">
          {SEARCH_MODE_OPTIONS.map((option) => {
            const isActive = mode === option.value;
            return (
              <button
                key={option.value}
                type="button"
                disabled={disabled}
                onClick={() => onModeChange(option.value)}
                className={`rounded-xl border px-4 py-2 text-left text-sm transition disabled:opacity-50 ${
                  isActive
                    ? "border-emerald-400/40 bg-emerald-400/[0.08] font-semibold text-white"
                    : "border-white/10 bg-white/[0.02] text-slate-300 hover:border-white/20 hover:bg-white/[0.04]"
                }`}
              >
                {option.label}
                <span className="mt-0.5 block text-[11px] font-normal text-slate-500">{option.hint}</span>
              </button>
            );
          })}
        </div>
      </div>

      {current && searchingKeywords && (
        <SelectableList
          label="Keywords"
          count={`${selectedKeywords.length} of ${allKeywords.length}`}
          disabled={disabled}
          onAll={() => onKeywordsChange([...allKeywords])}
          onNone={() => onKeywordsChange([])}
        >
          {allKeywords.map((keyword) => (
            <button
              key={keyword}
              type="button"
              disabled={disabled}
              onClick={() => onKeywordsChange(toggle(selectedKeywords, keyword))}
              className={`${CHIP_BASE} ${selectedKeywords.includes(keyword) ? CHIP_ON : CHIP_OFF}`}
            >
              {keyword}
            </button>
          ))}
        </SelectableList>
      )}

      {current && !searchingKeywords && (
        <SelectableList
          label="Commodity codes"
          count={`${selectedCodes.length} of ${allCodes.length}`}
          disabled={disabled}
          onAll={() => onCodesChange(allCodes.map((c) => c.code))}
          onNone={() => onCodesChange([])}
        >
          {allCodes.map((code) => (
            <button
              key={code.code}
              type="button"
              title={code.title}
              disabled={disabled}
              onClick={() => onCodesChange(toggle(selectedCodes, code.code))}
              className={`${CHIP_BASE} font-mono ${selectedCodes.includes(code.code) ? CHIP_ON : CHIP_OFF}`}
            >
              {code.code}
            </button>
          ))}
        </SelectableList>
      )}

      <div>
        <SectionLabel>
          Ad status {adStatuses.length === 0 && <span className="normal-case text-slate-600">— any</span>}
        </SectionLabel>
        <div className="flex flex-wrap gap-2">
          {AD_STATUS_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              disabled={disabled}
              onClick={() => onAdStatusChange(toggle(adStatuses, option.value))}
              className={`${CHIP_BASE} ${adStatuses.includes(option.value) ? CHIP_ON : CHIP_OFF}`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <div>
        <SectionLabel>
          Ad type {adTypes.length === 0 && <span className="normal-case text-slate-600">— any</span>}
        </SectionLabel>
        <div className="flex flex-wrap gap-2">
          {AD_TYPE_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              disabled={disabled}
              onClick={() => onAdTypeChange(toggle(adTypes, option.value))}
              className={`${CHIP_BASE} ${adTypes.includes(option.value) ? CHIP_ON : CHIP_OFF}`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function SelectableList({
  label,
  count,
  disabled,
  onAll,
  onNone,
  children,
}: {
  label: string;
  count: string;
  disabled?: boolean;
  onAll: () => void;
  onNone: () => void;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="flex items-center justify-between">
        <SectionLabel>
          {label} <span className="normal-case text-slate-600">— {count}</span>
        </SectionLabel>
        <div className="flex gap-2 pb-1 text-[11px]">
          <button type="button" disabled={disabled} onClick={onAll} className="text-slate-500 hover:text-slate-300 disabled:opacity-50">
            All
          </button>
          <span className="text-slate-700">·</span>
          <button type="button" disabled={disabled} onClick={onNone} className="text-slate-500 hover:text-slate-300 disabled:opacity-50">
            None
          </button>
        </div>
      </div>
      <div className="flex max-h-44 flex-wrap gap-2 overflow-y-auto rounded-xl border border-white/10 bg-slate-950/40 p-3">
        {children}
      </div>
    </div>
  );
}
