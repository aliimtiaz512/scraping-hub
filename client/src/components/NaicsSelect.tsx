"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getNaicsCodes, type NaicsResult } from "@/lib/api";

const DEBOUNCE_MS = 250;
const MAX_SUGGESTIONS = 8;

interface Props {
  /** The chosen 6-digit codes, in pick order. */
  selected: string[];
  onChange: (codes: string[]) => void;
  disabled?: boolean;
  placeholder?: string;
}

/**
 * Type-ahead picker over the NAICS reference table (`GET /naics?q=`), which
 * matches on code *or* title, so a partial code like "5415" completes to the
 * real six-digit codes already in the catalogue. Picks become removable chips;
 * the raw text box never holds the selection itself.
 */
export default function NaicsSelect({ selected, onChange, disabled, placeholder }: Props) {
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<NaicsResult[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  // Titles for the chips, learned as codes are picked (and for codes typed by
  // hand, backfilled from the catalogue when a lookup happens to return them).
  const [titles, setTitles] = useState<Record<string, string>>({});

  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const selectedSet = useMemo(() => new Set(selected), [selected]);

  // Debounced catalogue lookup. A blank query shows nothing — the standalone
  // NAICS console page is the place to browse all 1,000+ codes. The empty-query
  // reset and the "searching" flag are set by the typing handler, so this effect
  // only ever schedules work.
  useEffect(() => {
    const q = query.trim();
    if (!q) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const data = await getNaicsCodes(q, 1, MAX_SUGGESTIONS);
        if (cancelled) return;
        setSuggestions(data.results);
        setFailed(false);
        setActiveIndex(0);
        setTitles((prev) => {
          const next = { ...prev };
          for (const r of data.results) next[r.code] = r.title;
          return next;
        });
      } catch {
        if (!cancelled) {
          setSuggestions([]);
          setFailed(true);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

  // Close the list on an outside click.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  const add = useCallback(
    (code: string, title?: string) => {
      if (title) setTitles((prev) => ({ ...prev, [code]: title }));
      if (!selectedSet.has(code)) onChange([...selected, code]);
      setQuery("");
      setSuggestions([]);
      setOpen(false);
      inputRef.current?.focus();
    },
    [onChange, selected, selectedSet],
  );

  const remove = (code: string) => onChange(selected.filter((c) => c !== code));

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!suggestions.length) return;
      e.preventDefault();
      setOpen(true);
      setActiveIndex((i) => {
        const delta = e.key === "ArrowDown" ? 1 : -1;
        return (i + delta + suggestions.length) % suggestions.length;
      });
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const hit = open ? suggestions[activeIndex] : undefined;
      if (hit) {
        add(hit.code, hit.title);
        return;
      }
      // No highlighted suggestion — accept a hand-typed six-digit code so an
      // out-of-catalogue code is still usable.
      const raw = query.trim();
      if (/^\d{6}$/.test(raw)) add(raw);
      return;
    }
    if (e.key === "Escape") {
      setOpen(false);
      return;
    }
    if (e.key === "Backspace" && query === "" && selected.length) {
      remove(selected[selected.length - 1]);
    }
  };

  const showList = open && query.trim() !== "";

  return (
    <div ref={rootRef} className="relative">
      <input
        ref={inputRef}
        type="text"
        role="combobox"
        aria-expanded={showList}
        aria-controls="naics-suggestions"
        aria-autocomplete="list"
        autoComplete="off"
        value={query}
        disabled={disabled}
        onChange={(e) => {
          const value = e.target.value;
          setQuery(value);
          setOpen(true);
          if (value.trim()) {
            setLoading(true);
          } else {
            setSuggestions([]);
            setLoading(false);
            setFailed(false);
          }
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder ?? "Type a code or industry, e.g. 5415 or “software”"}
        className="w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm transition placeholder:text-ink-400 focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/25 disabled:cursor-not-allowed disabled:bg-ink-50 disabled:text-ink-400"
      />

      {showList && (
        <ul
          id="naics-suggestions"
          role="listbox"
          className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-lg border border-ink-200 bg-white py-1 shadow-lg shadow-ink-900/[0.08]"
        >
          {loading && suggestions.length === 0 && (
            <li className="px-3 py-2 text-xs text-ink-500">Searching the catalogue…</li>
          )}
          {!loading && failed && (
            <li className="px-3 py-2 text-xs text-ink-500">
              Catalogue unavailable — type the full six-digit code and press Enter.
            </li>
          )}
          {!loading && !failed && suggestions.length === 0 && (
            <li className="px-3 py-2 text-xs text-ink-500">
              No matching code. Refresh the catalogue on the NAICS page, or press Enter to use a
              six-digit code as typed.
            </li>
          )}
          {suggestions.map((s, i) => {
            const already = selectedSet.has(s.code);
            return (
              <li key={s.code} role="option" aria-selected={i === activeIndex}>
                <button
                  type="button"
                  // mousedown, not click: the input's blur must not close the
                  // list before the pick lands.
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => add(s.code, s.title)}
                  onMouseEnter={() => setActiveIndex(i)}
                  className={`flex w-full items-baseline gap-2.5 px-3 py-2 text-left transition ${
                    i === activeIndex ? "bg-gold-50" : "hover:bg-ink-50"
                  }`}
                >
                  <span className="font-mono text-xs font-medium text-ink-900">{s.code}</span>
                  <span className="min-w-0 flex-1 truncate text-xs text-ink-600">{s.title}</span>
                  {already && <span className="text-[10px] font-semibold text-gold-600">added</span>}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {selected.length > 0 && (
        <div className="mt-2.5 flex flex-wrap gap-2">
          {selected.map((code) => (
            <span
              key={code}
              title={titles[code] ?? code}
              className="inline-flex items-center gap-1.5 rounded-full border border-gold-200 bg-gold-50 py-1 pl-3 pr-1.5 text-xs font-medium text-gold-700"
            >
              <span className="font-mono">{code}</span>
              {titles[code] && (
                <span className="max-w-[16rem] truncate font-normal text-gold-600">
                  {titles[code]}
                </span>
              )}
              <button
                type="button"
                disabled={disabled}
                onClick={() => remove(code)}
                aria-label={`Remove NAICS ${code}`}
                className="flex h-4 w-4 items-center justify-center rounded-full text-gold-500 transition hover:bg-gold-200 hover:text-gold-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <svg
                  viewBox="0 0 10 10"
                  className="h-2.5 w-2.5"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  aria-hidden
                >
                  <path d="M1.5 1.5l7 7M8.5 1.5l-7 7" strokeLinecap="round" />
                </svg>
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
