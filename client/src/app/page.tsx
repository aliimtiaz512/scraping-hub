import Link from "next/link";

import LandingStats from "@/components/LandingStats";
import Logo from "@/components/Logo";
import PortalIcon from "@/components/PortalIcon";
import { Eyebrow } from "@/components/ui";
import { PORTALS, DEFAULT_PORTAL } from "@/lib/portals";

const STEPS = [
  {
    n: "01",
    title: "Choose a source and scope",
    body: "Pick a portal, then narrow to the niche, keywords, commodity codes or agency you actually bid on. Every filter mirrors the portal's own search.",
  },
  {
    n: "02",
    title: "The crawler does the reading",
    body: "A headless browser signs in, runs each search, pages through the results and downloads the documents attached to every solicitation it finds.",
  },
  {
    n: "03",
    title: "Take away a spreadsheet",
    body: "Results land in the database and an Excel workbook, filed under a per-run folder — with the run's full history kept so you can audit what happened.",
  },
];

export default function Landing() {
  return (
    <div className="flex min-h-screen flex-col">
      <LandingNav />

      <main className="flex-1">
        <Hero />
        <Sources />
        <HowItWorks />
        <ClosingCta />
      </main>

      <footer className="border-t border-ink-200/70 px-6 py-10">
        <div className="mx-auto flex max-w-6xl flex-col items-start justify-between gap-4 sm:flex-row sm:items-center">
          <div className="flex items-center gap-2.5">
            <Logo className="h-7 w-7" />
            <span className="font-display text-sm text-ink-900">Scraping Hub</span>
          </div>
          <p className="text-xs text-ink-400">
            Automated bid collection for public procurement portals.
          </p>
        </div>
      </footer>
    </div>
  );
}

function LandingNav() {
  return (
    <header className="sticky top-0 z-30 border-b border-ink-200/60 bg-paper/85 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-6">
        <Link href="/" className="flex items-center gap-2.5 transition hover:opacity-80">
          <Logo />
          <span className="font-display text-base text-ink-900">Scraping Hub</span>
        </Link>

        <nav className="hidden items-center gap-1 sm:flex" aria-label="Landing">
          <a href="#sources" className="rounded-lg px-3 py-1.5 text-sm font-medium text-ink-500 transition hover:bg-ink-50 hover:text-ink-900">
            Sources
          </a>
          <a href="#how" className="rounded-lg px-3 py-1.5 text-sm font-medium text-ink-500 transition hover:bg-ink-50 hover:text-ink-900">
            How it works
          </a>
        </nav>

        <Link
          href={`/console/${DEFAULT_PORTAL}`}
          className="inline-flex items-center gap-2 rounded-lg bg-ink-900 px-4 py-2 text-sm font-medium text-white shadow-sm ring-1 ring-ink-900 transition hover:bg-ink-800"
        >
          Open console
          <Arrow />
        </Link>
      </div>
    </header>
  );
}

function Hero() {
  return (
    <section className="relative overflow-hidden">
      {/* Warm wash behind the headline, fading into the paper canvas. */}
      <div
        className="pointer-events-none absolute inset-x-0 top-0 h-[36rem] bg-gradient-to-b from-gold-50 via-paper to-paper"
        aria-hidden
      />
      <div className="relative mx-auto max-w-6xl px-6 pb-20 pt-20 sm:pt-28">
        <div className="reveal" style={{ animationDelay: "0ms" }}>
          <Eyebrow>Bid intelligence</Eyebrow>
        </div>

        <h1
          className="reveal mt-6 max-w-3xl font-display text-5xl leading-[1.05] text-ink-900 sm:text-6xl md:text-7xl"
          style={{ animationDelay: "60ms" }}
        >
          Every public bid.
          <br />
          <span className="text-ink-500">Found automatically.</span>
        </h1>

        <p
          className="reveal mt-8 max-w-xl text-base leading-relaxed text-ink-600"
          style={{ animationDelay: "120ms" }}
        >
          Scraping Hub watches {PORTALS.length} public procurement portals, runs the searches you
          care about, collects every matching solicitation with its documents, and hands you a
          spreadsheet — without anyone clicking through a government website.
        </p>

        <div className="reveal mt-10 flex flex-wrap items-center gap-3" style={{ animationDelay: "180ms" }}>
          <Link
            href={`/console/${DEFAULT_PORTAL}`}
            className="inline-flex items-center gap-2 rounded-lg bg-ink-900 px-5 py-3 text-sm font-medium text-white shadow-sm ring-1 ring-ink-900 transition hover:bg-ink-800"
          >
            Open console
            <Arrow />
          </Link>
          <a
            href="#sources"
            className="inline-flex items-center gap-2 rounded-lg bg-white px-5 py-3 text-sm font-medium text-ink-800 shadow-sm ring-1 ring-ink-200 transition hover:bg-ink-50"
          >
            View sources
          </a>
        </div>

        <div className="reveal mt-16" style={{ animationDelay: "240ms" }}>
          <LandingStats />
        </div>
      </div>
    </section>
  );
}

function Sources() {
  return (
    <section id="sources" className="scroll-mt-16 border-t border-ink-200/70 bg-white/60">
      <div className="mx-auto max-w-6xl px-6 py-20">
        <Eyebrow>The sources</Eyebrow>
        <h2 className="mt-5 max-w-2xl font-display text-3xl leading-tight text-ink-900 sm:text-4xl">
          Four portals, one console.
        </h2>
        <p className="mt-4 max-w-xl text-sm leading-relaxed text-ink-600">
          Each source has its own login, search quirks and export format. Scraping Hub hides all of
          that behind one interface.
        </p>

        <div className="mt-12 grid gap-5 sm:grid-cols-2">
          {PORTALS.map((p) => (
            <Link
              key={p.key}
              href={`/console/${p.key}`}
              className="group relative flex flex-col overflow-hidden rounded-xl border border-ink-200/70 bg-white p-6 shadow-sm shadow-ink-900/[0.03] transition hover:-translate-y-0.5 hover:shadow-md hover:shadow-ink-900/[0.06]"
            >
              <div className="flex items-start justify-between gap-4">
                <span className={`flex h-11 w-11 items-center justify-center rounded-xl ring-1 ${p.accent.tile}`}>
                  <PortalIcon name={p.icon} className="h-5 w-5" />
                </span>
                <span className="text-ink-300 transition group-hover:translate-x-0.5 group-hover:text-gold-500">
                  <Arrow />
                </span>
              </div>

              <h3 className="mt-5 font-display text-xl text-ink-900">{p.label}</h3>
              <p className="mt-1 text-xs text-ink-500">{p.operator}</p>
              <p className="mt-4 flex-1 text-sm leading-relaxed text-ink-600">{p.description}</p>

              <div className="mt-6 flex flex-wrap gap-1.5">
                {p.outputs.map((o) => (
                  <span key={o} className="rounded-full bg-ink-50 px-2 py-0.5 text-xs font-medium text-ink-600">
                    {o}
                  </span>
                ))}
              </div>

              <p className="mt-5 border-t border-ink-100 pt-4 font-mono text-xs text-ink-400">{p.host}</p>
            </Link>
          ))}
        </div>
      </div>
    </section>
  );
}

function HowItWorks() {
  return (
    <section id="how" className="scroll-mt-16 border-t border-ink-200/70">
      <div className="mx-auto max-w-6xl px-6 py-20">
        <Eyebrow>How it works</Eyebrow>
        <h2 className="mt-5 max-w-2xl font-display text-3xl leading-tight text-ink-900 sm:text-4xl">
          Set the scope. Walk away.
        </h2>

        <div className="mt-12 grid gap-10 sm:grid-cols-3">
          {STEPS.map((step) => (
            <div key={step.n}>
              <span className="font-display text-sm text-gold-600">{step.n}</span>
              <div className="mt-3 h-px w-full bg-ink-200" />
              <h3 className="mt-5 font-display text-lg text-ink-900">{step.title}</h3>
              <p className="mt-2.5 text-sm leading-relaxed text-ink-600">{step.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function ClosingCta() {
  return (
    <section className="px-6 pb-20">
      <div className="relative mx-auto max-w-6xl overflow-hidden rounded-2xl bg-ink-900 px-8 py-16 text-center sm:px-16">
        {/* Gold bloom, kept faint so the navy stays the subject. */}
        <div
          className="pointer-events-none absolute -top-24 left-1/2 h-64 w-[36rem] -translate-x-1/2 rounded-full bg-gold-500/20 blur-3xl"
          aria-hidden
        />
        <div className="relative">
          <h2 className="mx-auto max-w-2xl font-display text-3xl leading-tight text-white sm:text-4xl">
            Stop reading procurement portals.
          </h2>
          <p className="mx-auto mt-5 max-w-lg text-sm leading-relaxed text-ink-300">
            Pick a source, choose your keywords, and let the crawler bring the work back to you.
          </p>
          <Link
            href={`/console/${DEFAULT_PORTAL}`}
            className="mt-9 inline-flex items-center gap-2 rounded-lg bg-white px-5 py-3 text-sm font-medium text-ink-900 shadow-sm transition hover:bg-gold-50"
          >
            Open console
            <Arrow />
          </Link>
        </div>
      </div>
    </section>
  );
}

function Arrow() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
      <path d="M3 8h10M9 4l4 4-4 4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
