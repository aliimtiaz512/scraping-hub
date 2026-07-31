"use client";

import { useMemo, useState } from "react";

import { Card, Chip, MiniButton } from "@/components/ui";
import type {
  BidnetDateFilter,
  BidnetDateSection,
  BidnetFilterCatalog,
  BidnetFilterSection,
  BidnetFilters as Filters,
} from "@/lib/api";

/**
 * Mirrors BidNet Direct's search sidebar in one card rather than a stack of
 * them: Status is always visible (three options, and every run has one), and
 * every other panel is a collapsed row showing its name and current selection.
 * One row opens at a time, so the form stays a single screen however many
 * options a panel holds.
 *
 * The selection language matches the portal: an empty list means "no
 * constraint", and Purchasing Group starts fully selected because that is how
 * BidNet ships it — the user unselects from a full set rather than building one.
 */

interface Props {
  catalog: BidnetFilterCatalog;
  filters: Filters;
  disabled?: boolean;
  refreshing?: boolean;
  onChange: (next: Filters) => void;
  onRefreshOptions: () => void;
}

/** Options above this get a filter box rather than an unusable wall of chips. */
const SEARCHABLE_THRESHOLD = 20;

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
}

export default function BidnetFilters({
  catalog,
  filters,
  disabled,
  refreshing,
  onChange,
  onRefreshOptions,
}: Props) {
  const [open, setOpen] = useState<string | null>(null);
  const status = filters.status ?? catalog.status.default;

  const selectionFor = (section: BidnetFilterSection): string[] => {
    const chosen = filters[section.name];
    if (chosen) return chosen;
    // Purchasing Group's "untouched" state is everything ticked, not nothing.
    return section.default_all ? section.options.map((o) => o.value) : [];
  };

  const activeCount =
    catalog.sections.filter((section) => isNarrowed(section, selectionFor(section))).length +
    catalog.dates.filter((section) => filters[section.name]).length;

  const toggleRow = (name: string) => setOpen((current) => (current === name ? null : name));

  return (
    <Card
      title="Filters"
      description="BidNet's own search sidebar. Every filter is optional except Status."
      actions={
        <>
          <span className="tabular mr-1 text-xs text-ink-500">
            {activeCount === 0 ? "None active" : `${activeCount} active`}
          </span>
          <MiniButton
            disabled={disabled || activeCount === 0}
            onClick={() => onChange({ status })}
          >
            Clear all
          </MiniButton>
        </>
      }
    >
      <div className="mb-4">
        <h4 className="mb-2 text-xs font-semibold text-ink-700">Status</h4>
        <div className="flex flex-wrap gap-2">
          {catalog.status.options.map((option) => (
            <Chip
              key={option.value}
              disabled={disabled}
              active={status === option.value}
              onClick={() => onChange({ ...filters, status: option.value })}
            >
              {option.label}
            </Chip>
          ))}
        </div>
      </div>

      <div className="divide-y divide-ink-100 border-t border-ink-100">
        {catalog.sections.map((section) => {
          const selected = selectionFor(section);
          return (
            <FilterRow
              key={section.name}
              label={section.label}
              summary={listSummary(section, selected)}
              narrowed={isNarrowed(section, selected)}
              open={open === section.name}
              onToggle={() => toggleRow(section.name)}
            >
              <ListFilter
                section={section}
                selected={selected}
                disabled={disabled}
                refreshing={refreshing}
                onChange={(values) => onChange({ ...filters, [section.name]: values })}
                onRefreshOptions={onRefreshOptions}
              />
            </FilterRow>
          );
        })}

        {catalog.dates.map((section) => {
          const value = filters[section.name] ?? null;
          return (
            <FilterRow
              key={section.name}
              label={section.label}
              summary={dateSummary(section, value)}
              narrowed={value !== null}
              open={open === section.name}
              onToggle={() => toggleRow(section.name)}
            >
              <DateFilterBody
                section={section}
                value={value}
                disabled={disabled}
                onChange={(next) => onChange({ ...filters, [section.name]: next })}
              />
            </FilterRow>
          );
        })}
      </div>

      <p className="mt-4 text-xs text-ink-500">
        {catalog.discovered_at
          ? `Filter options last read from BidNet on ${new Date(catalog.discovered_at).toLocaleString()}.`
          : "Filter options are the built-in catalog — open a flagged filter to read BidNet's complete set."}
      </p>
    </Card>
  );
}

/** One collapsible sidebar panel: name, current selection, and its controls. */
function FilterRow({
  label,
  summary,
  narrowed,
  open,
  onToggle,
  children,
}: {
  label: string;
  summary: string;
  /** True when this filter actually narrows the search — drives the accent. */
  narrowed: boolean;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-3 py-2.5 text-left transition hover:bg-ink-50/60"
      >
        <Chevron open={open} />
        <span className="flex-1 text-sm font-medium text-ink-800">{label}</span>
        <span className={`text-xs ${narrowed ? "font-medium text-gold-700" : "text-ink-400"}`}>
          {summary}
        </span>
      </button>
      {open && <div className="pb-4 pl-7 pr-1">{children}</div>}
    </div>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 16 16"
      aria-hidden
      className={`h-3 w-3 shrink-0 text-ink-400 transition-transform ${open ? "rotate-90" : ""}`}
      fill="currentColor"
    >
      <path d="M5.7 3.3a1 1 0 0 1 1.4 0l4 4a1 1 0 0 1 0 1.4l-4 4a1 1 0 1 1-1.4-1.4L9 8 5.7 4.7a1 1 0 0 1 0-1.4Z" />
    </svg>
  );
}

function ListFilter({
  section,
  selected,
  disabled,
  refreshing,
  onChange,
  onRefreshOptions,
}: {
  section: BidnetFilterSection;
  selected: string[];
  disabled?: boolean;
  refreshing?: boolean;
  onChange: (values: string[]) => void;
  onRefreshOptions: () => void;
}) {
  const [query, setQuery] = useState("");
  const searchable = section.options.length > SEARCHABLE_THRESHOLD;

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return section.options;
    return section.options.filter(
      (option) => option.label.toLowerCase().includes(needle) || option.value.includes(needle),
    );
  }, [section.options, query]);

  return (
    <div>
      <div className="mb-2.5 flex flex-wrap items-center gap-2">
        {searchable && (
          <input
            type="text"
            value={query}
            disabled={disabled}
            placeholder={`Filter ${section.label.toLowerCase()}…`}
            onChange={(event) => setQuery(event.target.value)}
            className="min-w-0 flex-1 rounded-lg border border-ink-200 bg-white px-3 py-1.5 text-sm text-ink-900 shadow-sm transition placeholder:text-ink-400 focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/25 disabled:cursor-not-allowed disabled:bg-ink-50"
          />
        )}
        <MiniButton
          disabled={disabled}
          onClick={() => onChange(section.options.map((o) => o.value))}
        >
          All
        </MiniButton>
        <MiniButton disabled={disabled} onClick={() => onChange([])}>
          None
        </MiniButton>
      </div>

      {section.partial && (
        <div className="mb-2.5 flex flex-wrap items-center gap-2 rounded-lg border border-gold-300 bg-gold-50/70 px-3 py-2 text-xs text-gold-800">
          <span className="flex-1">
            Showing the {section.options.length} options BidNet renders inline — the rest are behind
            its &ldquo;View All&rdquo; dialog.
          </span>
          <MiniButton disabled={disabled || refreshing} onClick={onRefreshOptions}>
            {refreshing ? "Refreshing…" : "Load all"}
          </MiniButton>
        </div>
      )}

      <div className="flex max-h-64 flex-wrap gap-2 overflow-y-auto">
        {visible.map((option) => (
          <Chip
            key={option.value}
            title={`${option.label} (${option.value})`}
            disabled={disabled}
            active={selected.includes(option.value)}
            onClick={() => onChange(toggle(selected, option.value))}
          >
            {option.label}
          </Chip>
        ))}
        {visible.length === 0 && (
          <p className="text-xs text-ink-500">No option matches &ldquo;{query}&rdquo;.</p>
        )}
      </div>

      {selected.length === 0 && section.default_all && (
        <p className="mt-2.5 text-xs text-red-600">
          Nothing selected — BidNet would return no results. Select at least one.
        </p>
      )}
    </div>
  );
}

function DateFilterBody({
  section,
  value,
  disabled,
  onChange,
}: {
  section: BidnetDateSection;
  value: BidnetDateFilter | null;
  disabled?: boolean;
  onChange: (next: BidnetDateFilter | null) => void;
}) {
  // The portal's date panels are mutually exclusive modes, so picking the active
  // one again clears the panel rather than leaving it stuck on.
  const pick = (type: string) => onChange(value?.type === type ? null : { type, within: "DAY" });

  return (
    <div>
      <div className="flex flex-wrap gap-2">
        {section.types.map((type) => (
          <Chip
            key={type.value}
            disabled={disabled}
            active={value?.type === type.value}
            onClick={() => pick(type.value)}
          >
            {type.label}
          </Chip>
        ))}
      </div>

      {value?.type === "WITHIN" && (
        <div className="mt-3">
          <label className="mb-1.5 block text-xs font-semibold text-ink-700">Period</label>
          <select
            value={value.within ?? "DAY"}
            disabled={disabled}
            onChange={(event) => onChange({ ...value, within: event.target.value })}
            className="rounded-lg border border-ink-200 bg-white px-3 py-1.5 text-sm text-ink-900 shadow-sm focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/25 disabled:cursor-not-allowed disabled:bg-ink-50"
          >
            {section.within_options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      )}

      {value?.type === "DAY" && (
        <DateInput
          label="Date"
          value={value.day ?? ""}
          disabled={disabled}
          onChange={(day) => onChange({ ...value, day })}
        />
      )}

      {value?.type === "RANGE" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <DateInput
            label="Starting"
            value={value.range_start ?? ""}
            disabled={disabled}
            onChange={(range_start) => onChange({ ...value, range_start })}
          />
          <DateInput
            label="Ending"
            value={value.range_end ?? ""}
            disabled={disabled}
            onChange={(range_end) => onChange({ ...value, range_end })}
          />
        </div>
      )}
    </div>
  );
}

/** A native date picker that emits the mm/dd/yyyy the portal's fields expect. */
function DateInput({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <div className="mt-3">
      <label className="mb-1.5 block text-xs font-semibold text-ink-700">{label}</label>
      <input
        type="date"
        value={toIsoDate(value)}
        disabled={disabled}
        onChange={(event) => onChange(toPortalDate(event.target.value))}
        className="rounded-lg border border-ink-200 bg-white px-3 py-1.5 text-sm text-ink-900 shadow-sm focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/25 disabled:cursor-not-allowed disabled:bg-ink-50"
      />
    </div>
  );
}

// -- summaries ---------------------------------------------------------------

/** True when a selection actually narrows the search — a full Purchasing Group
 *  list is the portal's own default, so it does not count. */
function isNarrowed(section: BidnetFilterSection, selected: string[]): boolean {
  return section.default_all ? selected.length < section.options.length : selected.length > 0;
}

function listSummary(section: BidnetFilterSection, selected: string[]): string {
  if (section.default_all) {
    return selected.length === section.options.length
      ? `All ${section.options.length}`
      : `${selected.length} of ${section.options.length}`;
  }
  if (selected.length === 0) return "Any";
  if (selected.length === 1) {
    return section.options.find((o) => o.value === selected[0])?.label ?? "1 selected";
  }
  return `${selected.length} selected`;
}

function dateSummary(section: BidnetDateSection, value: BidnetDateFilter | null): string {
  if (!value) return "Any";
  const label = section.types.find((t) => t.value === value.type)?.label ?? value.type;
  if (value.type === "WITHIN") {
    const period = section.within_options.find((o) => o.value === value.within)?.label ?? value.within;
    return `${label} ${period?.toLowerCase()}`;
  }
  if (value.type === "DAY") return value.day || "Pick a date";
  if (value.type === "RANGE") {
    return value.range_start && value.range_end
      ? `${value.range_start} – ${value.range_end}`
      : "Pick both dates";
  }
  return label;
}

/** mm/dd/yyyy (what the API takes) -> yyyy-mm-dd (what <input type="date"> takes). */
function toIsoDate(value: string): string {
  const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value);
  return match ? `${match[3]}-${match[1]}-${match[2]}` : "";
}

/** yyyy-mm-dd -> mm/dd/yyyy, the format BidNet's datepicker fields carry. */
function toPortalDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  return match ? `${match[2]}/${match[3]}/${match[1]}` : "";
}
