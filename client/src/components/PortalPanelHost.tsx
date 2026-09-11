"use client";

import { useState } from "react";

import PortalConsole from "@/components/PortalConsole";
import type { Portal } from "@/lib/api";

/**
 * Keep-alive host for the portal consoles.
 *
 * The console's "tabs" are routes, so moving from SAM to SEPTA used to unmount
 * the SAM page outright — losing not just the run (which the session registry
 * now holds) but everything else a panel had in hand: the dates typed into the
 * form, the NAICS codes picked, the niche selected, the scroll position, the
 * catalogues it had fetched.
 *
 * So the panels live here instead, in the console *layout*, which survives
 * navigation between portals. A portal is mounted the first time it is opened
 * and then kept in the DOM for the rest of the session, hidden rather than
 * removed when another portal is showing. Switching tabs is now a CSS toggle:
 * no unmount, no re-init, no reset.
 *
 * Mounted lazily rather than all twelve up front, because mounting a panel is
 * not free — several fetch their own catalogues (BidNet's filters, MyFlorida's
 * accounts, SAM's NAICS list) and eagerly mounting the lot would fire a dozen
 * requests at a user who asked for one portal.
 */
export default function PortalPanelHost({
  portal,
  visible,
}: {
  /** The portal the URL is pointing at, or null on a non-portal console route. */
  portal: Portal | null;
  /** False on the History and Downloads sections, which render their own page. */
  visible: boolean;
}) {
  const [mounted, setMounted] = useState<Portal[]>(() => (portal ? [portal] : []));

  // Set during render rather than in an effect: the portal being asked for has
  // to be in the list for *this* paint, or navigating to a new portal would
  // show an empty page for a frame before the panel appeared.
  if (portal && !mounted.includes(portal)) setMounted([...mounted, portal]);

  return (
    <>
      {mounted.map((p) => {
        const showing = visible && p === portal;
        return (
          // `hidden` rather than unmounting: the panel keeps running, it just
          // stops being painted. aria-hidden and inert keep a hidden panel out
          // of the accessibility tree and off the tab order, so the controls of
          // a background scrape are not reachable behind the visible one.
          <div key={p} hidden={!showing} aria-hidden={!showing} inert={!showing}>
            <PortalConsole portal={p} />
          </div>
        );
      })}
    </>
  );
}
