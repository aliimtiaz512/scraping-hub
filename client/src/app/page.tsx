"use client";

import { useState } from "react";

import BidnetPanel from "@/components/BidnetPanel";
import MyFloridaPanel from "@/components/MyFloridaPanel";
import RideMetroPanel from "@/components/RideMetroPanel";
import type { Portal } from "@/lib/api";

const PORTALS: {
  key: Portal;
  label: string;
  host: string;
  dot: string;
  glow: string;
}[] = [
  { key: "myflorida", label: "MyFlorida", host: "vendor.myfloridamarketplace.com", dot: "bg-sky-400", glow: "shadow-[0_0_10px_2px_rgba(56,189,248,0.7)]" },
  { key: "ridemetro", label: "RideMetro", host: "ridemetro.bonfirehub.com", dot: "bg-violet-400", glow: "shadow-[0_0_10px_2px_rgba(167,139,250,0.7)]" },
  { key: "bidnet", label: "BidNet Direct", host: "bidnetdirect.com", dot: "bg-emerald-400", glow: "shadow-[0_0_10px_2px_rgba(52,211,153,0.7)]" },
];

export default function Home() {
  const [portal, setPortal] = useState<Portal>("myflorida");
  const active = PORTALS.find((p) => p.key === portal)!;

  return (
    <main className="relative z-10 mx-auto flex min-h-screen max-w-5xl flex-col gap-8 px-5 py-10 sm:px-8">
      {/* Header */}
      <header className="flex flex-col gap-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-4">
            <RadarMark />
            <div>
              <h1 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">
                Scraping<span className="bg-gradient-to-r from-emerald-400 to-cyan-400 bg-clip-text text-transparent">Hub</span>
              </h1>
              <p className="mt-0.5 font-mono text-xs text-slate-500">
                bid crawler console · procurement portals
              </p>
            </div>
          </div>
          <div className="hidden items-center gap-2 rounded-full border border-emerald-400/20 bg-emerald-400/5 px-3 py-1.5 sm:flex">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-70" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
            </span>
            <span className="font-mono text-xs text-emerald-300">{PORTALS.length} targets online</span>
          </div>
        </div>

        {/* Portal switcher */}
        <div>
          <p className="mb-2 font-mono text-[11px] uppercase tracking-widest text-slate-600">{"// select target"}</p>
          <nav className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            {PORTALS.map((p) => {
              const isActive = p.key === portal;
              return (
                <button
                  key={p.key}
                  type="button"
                  onClick={() => setPortal(p.key)}
                  className={`group flex flex-1 items-center gap-3 rounded-xl border px-4 py-3 text-left transition ${
                    isActive
                      ? "border-white/15 bg-white/[0.06] shadow-[0_10px_40px_-15px_rgba(0,0,0,0.8)]"
                      : "border-white/5 bg-white/[0.02] hover:border-white/10 hover:bg-white/[0.04]"
                  }`}
                >
                  <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${p.dot} ${isActive ? p.glow : "opacity-50"}`} />
                  <span className="min-w-0">
                    <span className={`block text-sm font-semibold ${isActive ? "text-white" : "text-slate-300"}`}>
                      {p.label}
                    </span>
                    <span className="block truncate font-mono text-[11px] text-slate-500">{p.host}</span>
                  </span>
                </button>
              );
            })}
          </nav>
        </div>
      </header>

      {/* Active panel */}
      <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-5 backdrop-blur-sm sm:p-7">
        <div className="mb-5 flex items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${active.dot} ${active.glow}`} />
          <h2 className="text-sm font-semibold text-slate-200">{active.label}</h2>
          <span className="font-mono text-[11px] text-slate-600">/{active.key}</span>
        </div>
        {portal === "myflorida" && <MyFloridaPanel />}
        {portal === "ridemetro" && <RideMetroPanel />}
        {portal === "bidnet" && <BidnetPanel />}
      </section>

      <footer className="mt-auto pt-4 text-center font-mono text-[11px] text-slate-600">
        scraping-hub · selenium engine · fastapi backend
      </footer>
    </main>
  );
}

function RadarMark() {
  return (
    <div className="relative flex h-12 w-12 items-center justify-center">
      <div className="glow-pulse absolute inset-0 rounded-full bg-emerald-500/25 blur-xl" />
      <div className="relative flex h-12 w-12 items-center justify-center rounded-xl border border-emerald-400/25 bg-slate-950/60">
        <svg viewBox="0 0 48 48" className="h-8 w-8" fill="none">
          <circle cx="24" cy="24" r="18" stroke="rgba(52,211,153,0.35)" strokeWidth="1" />
          <circle cx="24" cy="24" r="11" stroke="rgba(52,211,153,0.25)" strokeWidth="1" />
          <circle cx="24" cy="24" r="2.5" fill="#34d399" />
          <g className="animate-sweep" style={{ transformOrigin: "24px 24px" }}>
            <path d="M24 24 L24 6 A18 18 0 0 1 40 18 Z" fill="url(#sweep)" />
          </g>
          <defs>
            <linearGradient id="sweep" x1="24" y1="24" x2="40" y2="10" gradientUnits="userSpaceOnUse">
              <stop stopColor="#34d399" stopOpacity="0.55" />
              <stop offset="1" stopColor="#34d399" stopOpacity="0" />
            </linearGradient>
          </defs>
        </svg>
      </div>
    </div>
  );
}
