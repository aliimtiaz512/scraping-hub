import Link from "next/link";

import Logo from "@/components/Logo";
import { DEFAULT_PORTAL } from "@/lib/portals";

export default function NotFound() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center px-6 text-center">
      <Logo className="h-11 w-11" />
      <p className="eyebrow mt-8 text-gold-600">Error 404</p>
      <h1 className="mt-4 font-display text-4xl text-ink-900">This page doesn’t exist.</h1>
      <p className="mt-4 max-w-sm text-sm leading-relaxed text-ink-500">
        The page you’re looking for has moved, or the address is wrong.
      </p>
      <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
        <Link
          href={`/console/${DEFAULT_PORTAL}`}
          className="inline-flex items-center rounded-lg bg-ink-900 px-5 py-3 text-sm font-medium text-white shadow-sm ring-1 ring-ink-900 transition hover:bg-ink-800"
        >
          Open console
        </Link>
        <Link
          href="/"
          className="inline-flex items-center rounded-lg bg-white px-5 py-3 text-sm font-medium text-ink-800 shadow-sm ring-1 ring-ink-200 transition hover:bg-ink-50"
        >
          Back home
        </Link>
      </div>
    </main>
  );
}
