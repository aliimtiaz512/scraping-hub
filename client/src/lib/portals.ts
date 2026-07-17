import type { Portal } from "@/lib/api";

/**
 * Presentation identity for each data source: the copy, iconography and accent
 * colour that make a portal recognisable across the sidebar, header and panel.
 *
 * Accent classes are written out in full because Tailwind only ships classes it
 * can see as complete strings — never build these by concatenation.
 */
export interface PortalMeta {
  key: Portal;
  label: string;
  /** Governing body / operator shown under the name in navigation. */
  operator: string;
  host: string;
  /** One-line pitch used in the sidebar tooltip and page header. */
  tagline: string;
  /** Full description shown in the panel hero. */
  description: string;
  /** What the run produces, listed as capability pills in the hero. */
  outputs: string[];
  icon: "florida" | "transit" | "network" | "capitol" | "prairie";
  accent: {
    /** Icon tile: background + foreground. */
    tile: string;
    /** Small dot used in dense rows. */
    dot: string;
    /** Left rail on the active sidebar item. */
    rail: string;
    /** Tinted wash behind the panel hero. */
    wash: string;
  };
}

export const PORTALS: PortalMeta[] = [
  {
    key: "myflorida",
    label: "MyFlorida",
    operator: "Florida Dept. of Management Services",
    host: "vendor.myfloridamarketplace.com",
    tagline: "Statewide vendor advertisements",
    description:
      "Search the Florida MarketPlace vendor advertisements by keyword or commodity code, then export every matching ad to a spreadsheet.",
    outputs: ["Keyword & code search", "Ad status filters", "Merged workbook"],
    icon: "florida",
    accent: {
      tile: "bg-sky-50 text-sky-600 ring-sky-100",
      dot: "bg-sky-500",
      rail: "bg-sky-500",
      wash: "from-sky-50",
    },
  },
  {
    key: "ridemetro",
    label: "RideMetro",
    operator: "Houston Metropolitan Transit Authority",
    host: "ridemetro.bonfirehub.com",
    tagline: "Open public opportunities",
    description:
      "Capture every open public opportunity published to the RideMetro Bonfire portal, with project details written to the database and an Excel sheet.",
    outputs: ["Full opportunity list", "Project details", "Excel export"],
    icon: "transit",
    accent: {
      tile: "bg-violet-50 text-violet-600 ring-violet-100",
      dot: "bg-violet-500",
      rail: "bg-violet-500",
      wash: "from-violet-50",
    },
  },
  {
    key: "bidnet",
    label: "BidNet Direct",
    operator: "Multi-state purchasing network",
    host: "bidnetdirect.com",
    tagline: "Member agency solicitations",
    description:
      "Search BidNet Direct one keyword at a time, filter to Member Agency Bids, and download the documents attached to every solicitation found.",
    outputs: ["Curated keyword catalog", "Document downloads", "Excel export"],
    icon: "network",
    accent: {
      tile: "bg-emerald-50 text-emerald-600 ring-emerald-100",
      dot: "bg-emerald-500",
      rail: "bg-emerald-500",
      wash: "from-emerald-50",
    },
  },
  {
    key: "wisconsin",
    label: "Wisconsin eSupplier",
    operator: "State of Wisconsin",
    host: "esupplier.wi.gov",
    tagline: "Current solicitations",
    description:
      "Search current Wisconsin solicitations by keyword, agency or NIGP code — or leave the fields blank to capture the entire current list.",
    outputs: ["Agency & NIGP filters", "Full event records", "Excel export"],
    icon: "capitol",
    accent: {
      tile: "bg-amber-50 text-amber-600 ring-amber-100",
      dot: "bg-amber-500",
      rail: "bg-amber-500",
      wash: "from-amber-50",
    },
  },
  {
    key: "northdakota",
    label: "North Dakota",
    operator: "State of North Dakota (ND Buys)",
    host: "public.ndbuys.nd.gov",
    tagline: "Public solicitation requests",
    description:
      "Sign in to ND Buys, open Public Solicitation Requests, and search by keyword (or commodity) — then capture every matching solicitation across the results grid to the database and an Excel sheet.",
    outputs: ["Keyword & commodity search", "Full solicitation grid", "Excel export"],
    icon: "prairie",
    accent: {
      tile: "bg-rose-50 text-rose-600 ring-rose-100",
      dot: "bg-rose-500",
      rail: "bg-rose-500",
      wash: "from-rose-50",
    },
  },
];

export function portalMeta(key: Portal): PortalMeta {
  return PORTALS.find((p) => p.key === key)!;
}

/** Narrows an untrusted URL segment to a known portal. */
export function isPortal(value: string): value is Portal {
  return PORTALS.some((p) => p.key === value);
}

/** The source a bare /console visit lands on. */
export const DEFAULT_PORTAL: Portal = "myflorida";
