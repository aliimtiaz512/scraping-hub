"use client";

import { Card, Chip } from "@/components/ui";
import type { BidnetKeywordLimits } from "@/lib/api";

/**
 * The keywords a run searches. Each line is typed into BidNet's own search box
 * (`#solicitationSingleBoxSearch`) and searched on its own — never concatenated —
 * so one line is one full search + pagination pass over the portal.
 *
 * A line may be a whole boolean expression: the search box documents `AND`, `OR`
 * and parenthesised grouping, and it is passed through verbatim.
 */

interface Props {
  value: string;
  limits: BidnetKeywordLimits | null;
  disabled?: boolean;
  onChange: (value: string) => void;
}

/** One search per line, blanks and duplicates dropped — mirrors the server's
 *  `clean_keywords`, so what the chips show is what the run will search. */
export function parseKeywords(value: string): string[] {
  return [...new Set(value.split("\n").map((line) => line.trim()).filter(Boolean))];
}

export default function BidnetKeywords({ value, limits, disabled, onChange }: Props) {
  const keywords = parseKeywords(value);
  const maxLength = limits?.max_keyword_length ?? 1000;
  const maxKeywords = limits?.max_keywords ?? 100;
  const tooLong = keywords.filter((keyword) => keyword.length > maxLength);
  const tooMany = keywords.length > maxKeywords;

  return (
    <Card
      title="Search keywords"
      description="One search per line. Each line goes into BidNet's search box on its own."
      actions={
        <span className="tabular text-xs text-ink-500">
          {keywords.length} {keywords.length === 1 ? "search" : "searches"}
        </span>
      }
    >
      <textarea
        value={value}
        disabled={disabled}
        rows={5}
        spellCheck={false}
        placeholder={"Construction\nDemolition AND Asbestos\n(Electrical AND Maintenance) OR Lighting"}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg border border-ink-200 bg-white px-3 py-2 font-mono text-sm leading-relaxed text-ink-900 shadow-sm transition placeholder:text-ink-400 focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/25 disabled:cursor-not-allowed disabled:bg-ink-50 disabled:text-ink-400"
      />

      <p className="mt-2 text-xs leading-relaxed text-ink-500">
        BidNet&rsquo;s search understands <code className="font-mono text-ink-700">AND</code>,{" "}
        <code className="font-mono text-ink-700">OR</code> (capitalised) and parentheses for grouping
        — e.g. <code className="font-mono text-ink-700">(Construction AND Demolition) OR Electrical</code>.
        Leave this empty to run the server&rsquo;s curated keyword catalog instead.
      </p>

      {keywords.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {keywords.map((keyword) => (
            <Chip key={keyword} mono active disabled title={`${keyword.length} characters`}>
              {keyword}
            </Chip>
          ))}
        </div>
      )}

      {tooMany && (
        <p className="mt-3 text-xs text-red-600">
          {keywords.length} searches is over the {maxKeywords} a single run allows — each one is a
          full pass over the portal.
        </p>
      )}
      {tooLong.length > 0 && (
        <p className="mt-3 text-xs text-red-600">
          {tooLong.length} {tooLong.length === 1 ? "line is" : "lines are"} longer than the{" "}
          {maxLength} characters BidNet&rsquo;s search box accepts.
        </p>
      )}
    </Card>
  );
}

/** Why a keyword set can't be run, or null when it is fine. */
export function keywordProblem(value: string, limits: BidnetKeywordLimits | null): string | null {
  const keywords = parseKeywords(value);
  const maxLength = limits?.max_keyword_length ?? 1000;
  const maxKeywords = limits?.max_keywords ?? 100;
  if (keywords.length > maxKeywords) return `Too many searches (max ${maxKeywords}).`;
  if (keywords.some((keyword) => keyword.length > maxLength)) {
    return `A keyword is longer than the ${maxLength} characters BidNet accepts.`;
  }
  return null;
}
