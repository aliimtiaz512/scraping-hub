"use client";

import type { AdStatus, AdType, Category, SearchMode } from "@/lib/api";
import { Card, Chip, MiniButton } from "@/components/ui";

const SEARCH_MODE_OPTIONS: { value: SearchMode; label: string; hint: string }[] = [
  { value: "keywords", label: "Keyword scraping", hint: "Runs one search per keyword" },
  { value: "codes", label: "Commodity code scraping", hint: "Runs a single search across all codes" },
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
      <Card title="Niche" description="Choose the industry whose keywords and commodity codes should be searched.">
        <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
          {categories.map((category) => {
            const isActive = selected === category.key;
            return (
              <button
                key={category.key}
                type="button"
                disabled={disabled}
                onClick={() => onSelect(category.key)}
                aria-pressed={isActive}
                className={`rounded-lg border p-3 text-left transition disabled:cursor-not-allowed disabled:opacity-50 ${
                  isActive
                    ? "border-gold-400 bg-gold-50/60 ring-1 ring-gold-400"
                    : "border-ink-200 bg-white hover:border-ink-300 hover:bg-ink-50"
                }`}
              >
                <span className={`block text-sm ${isActive ? "font-semibold text-ink-900" : "font-medium text-ink-800"}`}>
                  {category.label}
                </span>
                <span className={`tabular mt-0.5 block text-xs ${isActive ? "text-gold-700" : "text-ink-500"}`}>
                  {category.keywords.length} keywords · {category.codes.length} codes
                </span>
              </button>
            );
          })}
        </div>
      </Card>

      <Card title="Search method" description="How the portal's advanced search is driven for this run.">
        <div className="grid gap-2.5 sm:grid-cols-2">
          {SEARCH_MODE_OPTIONS.map((option) => {
            const isActive = mode === option.value;
            return (
              <button
                key={option.value}
                type="button"
                disabled={disabled}
                onClick={() => onModeChange(option.value)}
                aria-pressed={isActive}
                className={`flex items-start gap-3 rounded-lg border p-3 text-left transition disabled:cursor-not-allowed disabled:opacity-50 ${
                  isActive
                    ? "border-gold-400 bg-gold-50/60 ring-1 ring-gold-400"
                    : "border-ink-200 bg-white hover:border-ink-300 hover:bg-ink-50"
                }`}
              >
                <span
                  className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2 transition ${
                    isActive ? "border-gold-500" : "border-ink-300"
                  }`}
                >
                  {isActive && <span className="h-1.5 w-1.5 rounded-full bg-gold-500" />}
                </span>
                <span>
                  <span className={`block text-sm ${isActive ? "font-semibold text-ink-900" : "font-medium text-ink-800"}`}>
                    {option.label}
                  </span>
                  <span className={`mt-0.5 block text-xs ${isActive ? "text-gold-700" : "text-ink-500"}`}>{option.hint}</span>
                </span>
              </button>
            );
          })}
        </div>
      </Card>

      {current && searchingKeywords && (
        <SelectableList
          title="Keywords"
          description="Each selected keyword is searched separately."
          count={`${selectedKeywords.length} of ${allKeywords.length}`}
          disabled={disabled}
          onAll={() => onKeywordsChange([...allKeywords])}
          onNone={() => onKeywordsChange([])}
        >
          {allKeywords.map((keyword) => (
            <Chip
              key={keyword}
              disabled={disabled}
              active={selectedKeywords.includes(keyword)}
              onClick={() => onKeywordsChange(toggle(selectedKeywords, keyword))}
            >
              {keyword}
            </Chip>
          ))}
        </SelectableList>
      )}

      {current && !searchingKeywords && (
        <SelectableList
          title="Commodity codes"
          description="All selected codes are entered into a single search."
          count={`${selectedCodes.length} of ${allCodes.length}`}
          disabled={disabled}
          onAll={() => onCodesChange(allCodes.map((c) => c.code))}
          onNone={() => onCodesChange([])}
        >
          {allCodes.map((code) => (
            <Chip
              key={code.code}
              mono
              title={code.title}
              disabled={disabled}
              active={selectedCodes.includes(code.code)}
              onClick={() => onCodesChange(toggle(selectedCodes, code.code))}
            >
              {code.code}
            </Chip>
          ))}
        </SelectableList>
      )}

      <Card title="Filters" description="Leave a filter empty to accept every value the portal returns.">
        <div className="space-y-4">
          <div>
            <div className="mb-2 flex items-baseline gap-2">
              <h4 className="text-xs font-semibold text-ink-700">Ad status</h4>
              {adStatuses.length === 0 && <span className="text-xs text-ink-400">Any status</span>}
            </div>
            <div className="flex flex-wrap gap-2">
              {AD_STATUS_OPTIONS.map((option) => (
                <Chip
                  key={option.value}
                  disabled={disabled}
                  active={adStatuses.includes(option.value)}
                  onClick={() => onAdStatusChange(toggle(adStatuses, option.value))}
                >
                  {option.label}
                </Chip>
              ))}
            </div>
          </div>

          <div className="border-t border-ink-100 pt-4">
            <div className="mb-2 flex items-baseline gap-2">
              <h4 className="text-xs font-semibold text-ink-700">Ad type</h4>
              {adTypes.length === 0 && <span className="text-xs text-ink-400">Any type</span>}
            </div>
            <div className="flex flex-wrap gap-2">
              {AD_TYPE_OPTIONS.map((option) => (
                <Chip
                  key={option.value}
                  disabled={disabled}
                  active={adTypes.includes(option.value)}
                  onClick={() => onAdTypeChange(toggle(adTypes, option.value))}
                >
                  {option.label}
                </Chip>
              ))}
            </div>
          </div>
        </div>
      </Card>
    </div>
  );
}

function SelectableList({
  title,
  description,
  count,
  disabled,
  onAll,
  onNone,
  children,
}: {
  title: string;
  description: string;
  count: string;
  disabled?: boolean;
  onAll: () => void;
  onNone: () => void;
  children: React.ReactNode;
}) {
  return (
    <Card
      title={title}
      description={description}
      actions={
        <>
          <span className="tabular mr-1 text-xs text-ink-500">{count}</span>
          <MiniButton disabled={disabled} onClick={onAll}>
            Select all
          </MiniButton>
          <MiniButton disabled={disabled} onClick={onNone}>
            Clear
          </MiniButton>
        </>
      }
    >
      <div className="flex max-h-52 flex-wrap gap-2 overflow-y-auto rounded-lg border border-ink-200 bg-ink-50/60 p-3">
        {children}
      </div>
    </Card>
  );
}
