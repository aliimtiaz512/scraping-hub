"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import Logo from "@/components/Logo";
import PortalIcon from "@/components/PortalIcon";
import { LinkButton } from "@/components/ui";
import { apiDocsUrl, type Portal } from "@/lib/api";
import { PORTALS } from "@/lib/portals";

const SECTIONS = [
  { key: "", label: "Console" },
  { key: "history", label: "History" },
  { key: "exports", label: "Downloads" },
] as const;

/**
 * Chrome for every console route: global bar, source rail, footer.
 *
 * Both nav dimensions live in the URL (`/console/{portal}/{section}`), so the
 * shell derives its active state from the pathname rather than holding any.
 */
export default function ConsoleShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const segments = pathname.split("/").filter(Boolean); // ["console", portal, section?]
  const portal = (segments[1] ?? "") as Portal;
  const section = segments[2] ?? "";

  const href = (p: string, s: string) => `/console/${p}${s ? `/${s}` : ""}`;

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 border-b border-ink-200/70 bg-paper/85 backdrop-blur">
        <div className="flex h-16 items-center gap-3 px-4 sm:px-6">
          <Link href="/" className="flex shrink-0 items-center gap-2.5 rounded-lg transition hover:opacity-80">
            <Logo />
            <span className="font-display text-base text-ink-900">Scraping Hub</span>
          </Link>

          <span className="mx-1 hidden h-6 w-px bg-ink-200 sm:block" />

          <nav className="no-scrollbar flex min-w-0 flex-1 items-center gap-1 overflow-x-auto" aria-label="Sections">
            {SECTIONS.map((s) => {
              const isActive = s.key === section;
              return (
                <Link
                  key={s.key || "console"}
                  href={href(portal, s.key)}
                  aria-current={isActive ? "page" : undefined}
                  className={`relative shrink-0 rounded-lg px-3 pb-2 pt-1.5 text-sm transition ${
                    isActive ? "font-semibold text-ink-900" : "font-medium text-ink-500 hover:bg-ink-50 hover:text-ink-900"
                  }`}
                >
                  {s.label}
                  {/* Sits inside the link's box: the nav is overflow-x-auto, which
                      forces overflow-y:auto, so anything hanging below it would
                      raise a stray vertical scrollbar. */}
                  {isActive && <span className="absolute inset-x-3 bottom-0.5 h-0.5 rounded-full bg-gold-500" />}
                </Link>
              );
            })}
          </nav>

          <div className="hidden items-center gap-2 rounded-full border border-ink-200 bg-white px-2.5 py-1 md:flex">
            <span className="relative flex h-1.5 w-1.5">
              <span className="status-pulse absolute inline-flex h-full w-full rounded-full bg-emerald-400" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
            </span>
            <span className="whitespace-nowrap text-xs font-medium text-ink-600">{PORTALS.length} sources</span>
          </div>

          <LinkButton href={apiDocsUrl()} target="_blank" rel="noopener noreferrer" size="sm">
            <span className="hidden sm:inline">API docs</span>
            <svg viewBox="0 0 20 20" className="h-3.5 w-3.5 sm:hidden" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
              <path d="M5 3.5h6.5L15 7v9.5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-12a1 1 0 0 1 1-1Z" strokeLinejoin="round" />
              <path d="M11 3.5V7h4M6.75 10.5h6.5M6.75 13.5h4" strokeLinecap="round" />
            </svg>
          </LinkButton>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Source rail */}
        <aside className="sticky top-16 hidden h-[calc(100vh-4rem)] w-64 shrink-0 flex-col border-r border-ink-200/70 bg-white/60 lg:flex">
          <nav className="flex-1 overflow-y-auto p-3" aria-label="Data sources">
            <p className="eyebrow px-3 pb-2 pt-3 text-ink-400">Data sources</p>
            <ul className="space-y-1">
              {PORTALS.map((p) => {
                const isActive = p.key === portal;
                return (
                  <li key={p.key}>
                    <Link
                      href={href(p.key, section)}
                      aria-current={isActive ? "page" : undefined}
                      className={`group relative flex w-full items-center gap-3 rounded-lg px-3 py-2.5 transition ${
                        isActive ? "bg-white shadow-sm shadow-ink-900/[0.04] ring-1 ring-ink-200/70" : "hover:bg-white/70"
                      }`}
                    >
                      {isActive && (
                        <span className="absolute left-0 top-1/2 h-7 w-1 -translate-y-1/2 rounded-r-full bg-gold-500" />
                      )}
                      <span
                        className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ring-1 transition ${
                          isActive ? p.accent.tile : "bg-ink-50 text-ink-400 ring-ink-100 group-hover:text-ink-600"
                        }`}
                      >
                        <PortalIcon name={p.icon} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className={`block truncate text-sm ${isActive ? "font-semibold text-ink-900" : "font-medium text-ink-700"}`}>
                          {p.label}
                        </span>
                        <span className="block truncate text-xs text-ink-500">{p.tagline}</span>
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          {/* Source switcher for narrow screens */}
          <div className="border-b border-ink-200/70 bg-white/60 lg:hidden">
            <nav className="no-scrollbar flex gap-2 overflow-x-auto px-5 py-3" aria-label="Data sources">
              {PORTALS.map((p) => {
                const isActive = p.key === portal;
                return (
                  <Link
                    key={p.key}
                    href={href(p.key, section)}
                    aria-current={isActive ? "page" : undefined}
                    className={`flex shrink-0 items-center gap-2 rounded-full border px-3 py-1.5 text-sm transition ${
                      isActive ? "border-ink-900 bg-ink-900 font-medium text-white" : "border-ink-200 bg-white text-ink-600"
                    }`}
                  >
                    <span className={`h-1.5 w-1.5 rounded-full ${isActive ? "bg-gold-400" : p.accent.dot}`} />
                    {p.label}
                  </Link>
                );
              })}
            </nav>
          </div>

          <main className="mx-auto w-full max-w-5xl flex-1 px-5 py-8 sm:px-8">{children}</main>

          <footer className="border-t border-ink-200/70 px-5 py-5 sm:px-8">
            <p className="mx-auto max-w-5xl text-xs text-ink-400">
              Scraping Hub — automated bid collection for public procurement portals.
            </p>
          </footer>
        </div>
      </div>
    </div>
  );
}
