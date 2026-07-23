"use client";

import BidnetResults from "@/components/BidnetResults";
import NorthDakotaResults from "@/components/NorthDakotaResults";
import ResultsTable from "@/components/ResultsTable";
import RideMetroResults from "@/components/RideMetroResults";
import SamResults from "@/components/SamResults";
import SeptaResults from "@/components/SeptaResults";
import UnisonResults from "@/components/UnisonResults";
import WisconsinResults from "@/components/WisconsinResults";
import type { BidResult, Portal } from "@/lib/api";

/**
 * Picks the results table matching a portal's row shape. Each portal returns
 * different columns, so the tables can't be merged — this just routes to them.
 */
export default function PortalResults({ portal, bids }: { portal: Portal; bids: BidResult[] }) {
  switch (portal) {
    case "myflorida":
      return <ResultsTable bids={bids} />;
    case "ridemetro":
      return <RideMetroResults bids={bids} />;
    case "bidnet":
      return <BidnetResults bids={bids} />;
    case "wisconsin":
      return <WisconsinResults bids={bids} />;
    case "northdakota":
      return <NorthDakotaResults bids={bids} />;
    case "septa":
      return <SeptaResults bids={bids} />;
    case "sam":
      return <SamResults bids={bids} />;
    case "unison":
      return <UnisonResults bids={bids} />;
    case "naics":
      return null; // NAICS is a reference tool, not a bid list
  }
}
