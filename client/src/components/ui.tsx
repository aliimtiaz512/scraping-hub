"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

/** Emerald call-to-action used to launch a scrape across every portal. */
export function StartButton({
  running,
  starting,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { running?: boolean; starting?: boolean }) {
  const label = running ? "Crawling…" : starting ? "Starting…" : children;
  return (
    <button
      type="button"
      {...props}
      className="group inline-flex items-center gap-2 rounded-xl bg-gradient-to-b from-emerald-400 to-emerald-500 px-5 py-2.5 text-sm font-semibold text-emerald-950 shadow-[0_0_24px_-6px_rgba(16,185,129,0.8)] transition hover:from-emerald-300 hover:to-emerald-400 disabled:cursor-not-allowed disabled:from-slate-700 disabled:to-slate-700 disabled:text-slate-400 disabled:shadow-none"
    >
      {running || starting ? (
        <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-emerald-950/40 border-t-emerald-950" />
      ) : (
        <PlayIcon />
      )}
      {label}
    </button>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 rounded-xl border border-red-500/20 bg-red-500/[0.07] px-4 py-3 text-sm text-red-300">
      <span className="mt-0.5 font-mono text-red-400">!</span>
      <span>{message}</span>
    </div>
  );
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <h3 className="mb-2 font-mono text-[11px] uppercase tracking-widest text-slate-500">{children}</h3>
  );
}

/** Shared dark table shell: bordered, scrollable, with a mono uppercase header row. */
export function DataTable({ headers, children }: { headers: { label: string; className?: string }[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-2xl border border-white/10 bg-slate-950/40">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/10 text-left font-mono text-[10px] uppercase tracking-widest text-slate-500">
            {headers.map((h) => (
              <th key={h.label} className={`px-4 py-2.5 font-medium ${h.className ?? ""}`}>
                {h.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

/** Per-row outcome badge shared by every results table. */
export function DocStatus({ state, title }: { state: "ok" | "partial" | "failed" | "empty"; title?: string }) {
  const map = {
    ok: { cls: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300", text: "ok" },
    partial: { cls: "border-amber-400/30 bg-amber-400/10 text-amber-300", text: "partial" },
    failed: { cls: "border-red-400/30 bg-red-400/10 text-red-300", text: "failed" },
    empty: { cls: "border-slate-500/30 bg-slate-500/10 text-slate-400", text: "no docs" },
  }[state];
  return (
    <span className={`rounded-full border px-2 py-0.5 font-mono text-[10px] ${map.cls}`} title={title}>
      {map.text}
    </span>
  );
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="currentColor" aria-hidden>
      <path d="M4 3.5v9a.5.5 0 0 0 .77.42l7-4.5a.5.5 0 0 0 0-.84l-7-4.5A.5.5 0 0 0 4 3.5Z" />
    </svg>
  );
}
