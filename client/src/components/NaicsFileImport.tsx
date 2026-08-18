"use client";

import { useRef, useState } from "react";

import { importNaicsFile, type NaicsImportResult } from "@/lib/api";

const ACCEPT = ".csv,.xlsx,.xls";

/**
 * Load a list of NAICS codes from a spreadsheet into the picker beside it.
 *
 * One file at a time, and a new one **replaces** what the last one loaded
 * rather than adding to it — a list imported twice is the most likely way to
 * end up searching codes nobody meant to include, and "replace" is what the
 * word "upload" implies to the person clicking it.
 *
 * The parsing is the server's (see `app/scrapers/naics/importer.py`): it holds
 * the reference table, which is what lets a five-digit group be expanded to its
 * real children and an invented code be rejected before it reaches a run.
 *
 * What comes back is reported in full — how many codes, what was expanded, what
 * was dropped and why. An import that silently takes 40 rows of a 45-row file
 * is one whose run searches less than it was given, and nobody finds out.
 */
export default function NaicsFileImport({
  onCodes,
  disabled,
}: {
  /** Called with the parsed codes. Replaces the current selection. */
  onCodes: (codes: string[]) => void;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<NaicsImportResult | null>(null);
  const [fileName, setFileName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const parsed = await importNaicsFile(file);
      setFileName(file.name);
      setResult(parsed);
      onCodes(parsed.codes);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      // Cleared so choosing the same file again re-runs the import; without
      // this the input holds the value and the change event never fires.
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  return (
    <div className="mt-2">
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="sr-only"
        disabled={disabled || busy}
        onChange={(e) => void handleFile(e.target.files?.[0])}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={disabled || busy}
        className="inline-flex items-center gap-1.5 rounded-lg border border-ink-200 bg-white px-3 py-1.5 text-xs font-semibold text-ink-700 shadow-sm transition hover:border-gold-400 hover:text-ink-900 focus:outline-none focus:ring-2 focus:ring-gold-400/30 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <span aria-hidden="true">📁</span>
        {busy ? "Reading file…" : "Upload NAICS file"}
      </button>
      <span className="ml-2 text-xs text-ink-500">.csv, .xlsx or .xls — one file, replaces the list</span>

      {error && (
        <p className="mt-2 text-xs leading-relaxed text-red-700">{error}</p>
      )}

      {result && (
        <div className="mt-2 rounded-lg border border-ink-200/70 bg-ink-50/60 px-3 py-2 text-xs leading-relaxed text-ink-700">
          <p>
            <span className="font-semibold text-ink-900">
              {result.count} NAICS code{result.count === 1 ? "" : "s"} loaded
            </span>{" "}
            from <span className="font-mono">{fileName}</span> — read from {result.source}.
          </p>

          {result.expanded.length > 0 && (
            <p className="mt-1">
              {result.expanded
                .map((e) => `${e.entry} expanded to its ${e.codes} six-digit codes`)
                .join("; ")}
              .
            </p>
          )}

          {result.duplicates > 0 && (
            <p className="mt-1">
              {result.duplicates} duplicate{result.duplicates === 1 ? "" : "s"} removed.
            </p>
          )}

          {result.skipped.length > 0 && (
            <details className="mt-1">
              <summary className="cursor-pointer font-semibold text-ink-900">
                {result.skipped.length} entr{result.skipped.length === 1 ? "y" : "ies"} skipped
              </summary>
              <ul className="mt-1 space-y-0.5 pl-4">
                {result.skipped.slice(0, 20).map((s, i) => (
                  <li key={`${s.value}-${i}`} className="list-disc">
                    <span className="font-mono">{s.value || "(blank)"}</span> — {s.reason}
                  </li>
                ))}
                {result.skipped.length > 20 && (
                  <li className="list-disc">…and {result.skipped.length - 20} more</li>
                )}
              </ul>
            </details>
          )}

          {result.count === 0 && (
            <p className="mt-1 text-red-700">
              No usable codes in this file — check that the codes are in a column headed
              NAICS, or in the first column.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
