"use client";

import BidnetPanel from "@/components/BidnetPanel";
import MyFloridaPanel from "@/components/MyFloridaPanel";
import PortalIcon from "@/components/PortalIcon";
import RideMetroPanel from "@/components/RideMetroPanel";
import WisconsinPanel from "@/components/WisconsinPanel";
import type { Portal } from "@/lib/api";
import { portalMeta, type PortalMeta } from "@/lib/portals";

export default function PortalConsole({ portal }: { portal: Portal }) {
  const meta = portalMeta(portal);
  return (
    <div key={portal} className="rise-in space-y-6">
      <PortalHero meta={meta} />
      {portal === "myflorida" && <MyFloridaPanel />}
      {portal === "ridemetro" && <RideMetroPanel />}
      {portal === "bidnet" && <BidnetPanel />}
      {portal === "wisconsin" && <WisconsinPanel />}
    </div>
  );
}

/** Identity block at the top of each source's console page. */
function PortalHero({ meta }: { meta: PortalMeta }) {
  return (
    <header
      className={`overflow-hidden rounded-xl border border-ink-200/70 bg-gradient-to-br ${meta.accent.wash} via-white to-white shadow-sm shadow-ink-900/[0.03]`}
    >
      <div className="p-6 sm:p-8">
        <div className="flex items-start gap-4">
          <span className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-xl ring-1 ${meta.accent.tile}`}>
            <PortalIcon name={meta.icon} className="h-6 w-6" />
          </span>
          <div className="min-w-0">
            <h1 className="font-display text-2xl text-ink-900 sm:text-3xl">{meta.label}</h1>
            <p className="mt-1 text-sm text-ink-500">{meta.operator}</p>
          </div>
        </div>

        <div className="mt-5 h-px w-12 bg-gold-400" />

        <p className="mt-5 max-w-2xl text-sm leading-relaxed text-ink-600">{meta.description}</p>

        <div className="mt-6 flex flex-wrap items-center gap-2">
          {meta.outputs.map((output) => (
            <span
              key={output}
              className="rounded-full border border-ink-200 bg-white/70 px-2.5 py-1 text-xs font-medium text-ink-600"
            >
              {output}
            </span>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-2 border-t border-ink-200/60 bg-white/60 px-6 py-3 sm:px-8">
        <svg viewBox="0 0 16 16" className="h-3.5 w-3.5 shrink-0 text-ink-400" fill="currentColor" aria-hidden>
          <path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1ZM2.5 8c0-.6.1-1.2.3-1.8l3.3 3.3v.7c0 .7.6 1.3 1.3 1.3v1.3A5.5 5.5 0 0 1 2.5 8Zm9.2 3.5c-.2-.5-.7-.9-1.3-.9h-.7V8.7c0-.4-.3-.7-.7-.7H5.4V6.7h1.3c.4 0 .7-.3.7-.7V4.7h1.3c.7 0 1.3-.6 1.3-1.3v-.3a5.5 5.5 0 0 1 1.7 8.4Z" />
        </svg>
        <span className="truncate font-mono text-xs text-ink-500">{meta.host}</span>
      </div>
    </header>
  );
}
