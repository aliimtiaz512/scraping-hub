import type { Metadata } from "next";
import { notFound } from "next/navigation";

import RunHistory from "@/components/RunHistory";
import { isPortal, PORTALS, portalMeta } from "@/lib/portals";

export function generateStaticParams() {
  return PORTALS.map((p) => ({ portal: p.key }));
}

export async function generateMetadata({ params }: { params: Promise<{ portal: string }> }): Promise<Metadata> {
  const { portal } = await params;
  if (!isPortal(portal)) return { title: "Not found" };
  return { title: `${portalMeta(portal).label} run history` };
}

export default async function HistoryPage({ params }: { params: Promise<{ portal: string }> }) {
  const { portal } = await params;
  if (!isPortal(portal)) notFound();
  return <RunHistory key={portal} meta={portalMeta(portal)} />;
}
