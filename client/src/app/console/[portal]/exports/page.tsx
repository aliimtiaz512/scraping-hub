import type { Metadata } from "next";
import { notFound } from "next/navigation";

import ExportsPanel from "@/components/ExportsPanel";
import { isPortal, PORTALS, portalMeta } from "@/lib/portals";

export function generateStaticParams() {
  return PORTALS.map((p) => ({ portal: p.key }));
}

export async function generateMetadata({ params }: { params: Promise<{ portal: string }> }): Promise<Metadata> {
  const { portal } = await params;
  if (!isPortal(portal)) return { title: "Not found" };
  return { title: `${portalMeta(portal).label} exports` };
}

export default async function ExportsPage({ params }: { params: Promise<{ portal: string }> }) {
  const { portal } = await params;
  if (!isPortal(portal)) notFound();
  return <ExportsPanel key={portal} meta={portalMeta(portal)} />;
}
