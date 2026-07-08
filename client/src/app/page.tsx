"use client";

import { useState } from "react";

import BidnetPanel from "@/components/BidnetPanel";
import MyFloridaPanel from "@/components/MyFloridaPanel";
import RideMetroPanel from "@/components/RideMetroPanel";
import type { Portal } from "@/lib/api";

const PORTALS: { key: Portal; label: string }[] = [
  { key: "myflorida", label: "MyFlorida" },
  { key: "ridemetro", label: "RideMetro" },
  { key: "bidnet", label: "BidNet Direct" },
];

export default function Home() {
  const [portal, setPortal] = useState<Portal>("myflorida");

  return (
    <main className="mx-auto max-w-4xl space-y-6 p-6">
      <header>
        <h1 className="text-2xl font-bold text-slate-900">Scraping Hub</h1>
        <p className="text-sm text-slate-500">Bid scrapers for public procurement portals.</p>
      </header>

      <div className="flex gap-1 border-b border-slate-200">
        {PORTALS.map((p) => (
          <button
            key={p.key}
            type="button"
            onClick={() => setPortal(p.key)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition ${
              portal === p.key
                ? "border-blue-600 text-blue-700"
                : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>

      {portal === "myflorida" && <MyFloridaPanel />}
      {portal === "ridemetro" && <RideMetroPanel />}
      {portal === "bidnet" && <BidnetPanel />}
    </main>
  );
}
