import type { Metadata } from "next";
import { notFound } from "next/navigation";

import PortalConsole from "@/components/PortalConsole";
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

export default async function ConsolePage({ params }: { params: Promise<{ portal: string }> }) {
  const { portal } = await params;
  if (!isPortal(portal)) notFound();
  return <PortalConsole portal={portal} />;
}
