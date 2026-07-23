import type { PortalMeta } from "@/lib/portals";

/**
 * Line-art glyph per data source. Each one hints at the issuing body — a coast,
 * a transit line, a network, a capitol — so sources stay distinguishable at a
 * glance in the sidebar.
 */
export default function PortalIcon({ name, className = "h-5 w-5" }: { name: PortalMeta["icon"]; className?: string }) {
  const common = {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    className,
    "aria-hidden": true,
  };

  switch (name) {
    case "florida":
      // Sun over water: the state's coastal procurement marketplace.
      return (
        <svg {...common}>
          <circle cx="12" cy="8" r="3.25" />
          <path d="M12 2.5v1M12 12.5v1M17.5 8h1M5.5 8h1M15.9 4.1l.7-.7M7.4 12.6l.7-.7M15.9 11.9l.7.7M7.4 3.4l.7.7" />
          <path d="M3 17.5c1.8 0 1.8 1.5 3.6 1.5s1.8-1.5 3.6-1.5 1.8 1.5 3.6 1.5 1.8-1.5 3.6-1.5 1.8 1.5 3.6 1.5" />
        </svg>
      );
    case "transit":
      // Transit car on a line.
      return (
        <svg {...common}>
          <rect x="5" y="3.5" width="14" height="13" rx="3" />
          <path d="M5 11h14M9 3.5v13M15 3.5v13" />
          <circle cx="8.5" cy="13.75" r=".85" fill="currentColor" stroke="none" />
          <circle cx="15.5" cy="13.75" r=".85" fill="currentColor" stroke="none" />
          <path d="M8 20.5l-1.5 2M16 20.5l1.5 2M4 20.5h16" />
        </svg>
      );
    case "network":
      // Hub and spokes: a multi-agency purchasing network.
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="2.5" />
          <circle cx="12" cy="4" r="1.75" />
          <circle cx="19" cy="16" r="1.75" />
          <circle cx="5" cy="16" r="1.75" />
          <path d="M12 9.5v-3.6M14.2 13.4l3.2 1.8M9.8 13.4l-3.2 1.8" />
        </svg>
      );
    case "capitol":
      // Government building: the state supplier system.
      return (
        <svg {...common}>
          <path d="M12 2.5l8 4.5H4l8-4.5Z" />
          <path d="M6.5 10v7M10 10v7M14 10v7M17.5 10v7" />
          <path d="M4 20.5h16M3.5 17.5h17" />
        </svg>
      );
    case "prairie":
      // Wheat and horizon: North Dakota's northern plains.
      return (
        <svg {...common}>
          <path d="M12 4v13" />
          <path d="M12 7.5c-1.4-.3-2.6-1.1-3-2.6 1.5 0 2.7.6 3 2.6ZM12 7.5c1.4-.3 2.6-1.1 3-2.6-1.5 0-2.7.6-3 2.6Z" />
          <path d="M12 11c-1.4-.3-2.6-1.1-3-2.6 1.5 0 2.7.6 3 2.6ZM12 11c1.4-.3 2.6-1.1 3-2.6-1.5 0-2.7.6-3 2.6Z" />
          <path d="M4 20.5h16" />
        </svg>
      );
    case "rail":
      // Regional rail signal: SEPTA's commuter rail network.
      return (
        <svg {...common}>
          <path d="M7 3.5h10a1 1 0 0 1 1 1V14a2.5 2.5 0 0 1-2.5 2.5h-5A2.5 2.5 0 0 1 8 14V4.5a1 1 0 0 1 1-1Z" />
          <path d="M8 9.5h10" />
          <circle cx="10.5" cy="12.75" r=".85" fill="currentColor" stroke="none" />
          <circle cx="15.5" cy="12.75" r=".85" fill="currentColor" stroke="none" />
          <path d="M9.5 16.5 7.5 20.5M16.5 16.5l2 4" />
        </svg>
      );
    case "federal":
      // Federal shield: SAM.gov's US government contract opportunities.
      return (
        <svg {...common}>
          <path d="M12 2.5l7 2.5v5c0 4.4-2.9 8.3-7 9.5-4.1-1.2-7-5.1-7-9.5v-5l7-2.5Z" />
          <path d="M9.2 11.5l2 2 3.6-4" />
        </svg>
      );
    case "marketplace":
      // Storefront/awning: the Unison buyer marketplace.
      return (
        <svg {...common}>
          <path d="M4 9.5 5.5 4h13L20 9.5" />
          <path d="M4 9.5c0 1.4 1.1 2.5 2.5 2.5S9 10.9 9 9.5c0 1.4 1.1 2.5 2.5 2.5S14 10.9 14 9.5c0 1.4 1.1 2.5 2.5 2.5S19 10.9 19 9.5" />
          <path d="M5.5 12v8.5h13V12" />
          <path d="M10 20.5v-5h4v5" />
        </svg>
      );
    case "catalog":
      // Indexed list: the searchable NAICS code reference.
      return (
        <svg {...common}>
          <rect x="4.5" y="3.5" width="15" height="17" rx="2" />
          <path d="M8 8h8M8 12h8M8 16h5" />
          <path d="M4.5 8h1.5M4.5 12h1.5M4.5 16h1.5" />
        </svg>
      );
  }
}
