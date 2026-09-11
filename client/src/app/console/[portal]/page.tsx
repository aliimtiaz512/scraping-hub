import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { isPortal, PORTALS, portalMeta } from "@/lib/portals";

export function generateStaticParams() {
  return PORTALS.map((p) => ({ portal: p.key }));
}

export async function generateMetadata({ params }: { params: Promise<{ portal: string }> }): Promise<Metadata> {
  const { portal } = await params;
  if (!isPortal(portal)) return { title: "Not found" };
  const meta = portalMeta(portal);
  return { title: meta.label, description: meta.description };
}

/**
 * This route renders nothing on purpose.
 *
 * The portal console itself is rendered by the console *layout*, through
 * `PortalPanelHost`, because a page is unmounted the moment you navigate to
 * another portal — taking a running scrape's view, logs and form with it. The
 * layout survives that navigation, so the panels live there and are kept alive
 * across tab switches.
 *
 * What stays here is everything that genuinely belongs to the route: the
 * per-portal metadata, the static params, and the 404 for an unknown portal.
 */
export default async function ConsolePage({ params }: { params: Promise<{ portal: string }> }) {
  const { portal } = await params;
  if (!isPortal(portal)) notFound();
  return null;
}
