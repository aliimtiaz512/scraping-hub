"""BidNet Direct sidebar filter catalog — the contract between the UI and the DOM.

The search page's left column (`div.leftColumn > #searchFilterDiv`) is a stack of
`mets-panel` accordions, one per filter. This module is the single source of truth
for how each of those panels is addressed:

* a stable **API name** the frontend and the scrape request speak in
  (``locations``), mapped to
* the portal's **section key** (``regionId``) — the value the page uses for its
  ``data-filter-section`` attribute, its ``sectionKeyExpandedStatus[...]`` input
  and its "View All" lightbox URL, and
* the **DOM handles** the Selenium layer drives (panel body id, hidden
  ``filterHiddenField`` id, the empty sentinel that field holds when nothing is
  selected).

Every list panel shares one shape, which is what makes a single generic driver
possible (see `sidebar.py`):

    <div id="panel{SECTION}-body" data-filter-section="{SECTION}" class="auto-search filterPanel …">
      <input id="{FIELD}" name="{FIELD}" class="filterHiddenField" type="hidden" value="{csv of selected values}">
      <ul>
        <li data-filter-item-value="{VALUE}">
          … <input type="checkbox" id="g_NNN" data-filter-item-value="{VALUE}"> <span class="inputText">{LABEL}</span>
        </li>
        …
      </ul>
      <div class="linksPanel"><a id="viewAll{SECTION}" …>View All</a></div>
    </div>

The generated ``g_NNN`` ids are re-numbered on every render, so nothing here ever
targets them — `data-filter-item-value` is the stable handle.

Only ~12 highest-count options are rendered inline per panel; the rest live behind
"View All". `OPTIONS` therefore seeds what the page ships inline (plus the fully
derived Location list) and is *merged over* by whatever the discovery pass has
harvested into the on-disk cache — see `load_options`/`save_discovered`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.config import SERVER_ROOT

logger = logging.getLogger(__name__)

# Discovered-option cache: written by the "refresh filter options" run, read on
# every GET /bidnet/filters. Kept outside the package's source tree so a redeploy
# never ships a stale harvest.
OPTIONS_CACHE = SERVER_ROOT / "data" / "bidnet_filter_options.json"


# -- section specs ----------------------------------------------------------

class Section(BaseModel):
    """One sidebar panel: its API name and everything needed to drive its DOM."""

    name: str                      # API/request field name
    label: str                     # heading as it reads in the sidebar
    section_key: str               # data-filter-section / sectionKeyExpandedStatus key
    panel_body_id: str             # #<id> of the panel body holding the <ul>
    hidden_field_id: str           # #<id> of the .filterHiddenField carrying the selection
    view_all_id: str               # #<id> of the "View All" link into the lightbox
    # What the hidden field holds when nothing is selected. Location is the odd
    # one out — it ships the literal sentinel below rather than an empty string.
    empty_value: str = ""
    # Purchasing Group arrives with every option ticked; the user unselects from
    # a full set instead of building one up. Every other list starts empty.
    default_all: bool = False


# The list panels, in sidebar order. Status and the two date panels are not here:
# they are radio/date controls rather than value lists (see STATUS_* / DATE_*).
SECTIONS: list[Section] = [
    Section(
        name="nigp_categories",
        label="NIGP Categories",
        section_key="categories.NIGP_SS",
        panel_body_id="panelcategoriesNIGP_SS-body",
        hidden_field_id="categorySelectionNIGP_SS",
        view_all_id="viewAllcategoriesNIGP_SS",
    ),
    Section(
        name="organizations",
        label="Organization",
        section_key="buyerOrganizationId",
        panel_body_id="panelbuyerOrganizationId-body",
        hidden_field_id="buyerOrganizationId",
        view_all_id="viewAllbuyerOrganizationId",
    ),
    Section(
        name="locations",
        label="Location",
        section_key="regionId",
        panel_body_id="panelregionId-body",
        hidden_field_id="regionId",
        view_all_id="viewAllregionId",
        # The page ships this sentinel, not "", in an unselected Location field.
        empty_value="[null-null]{}",
    ),
    Section(
        name="purchasing_groups",
        label="Purchasing Group",
        section_key="solicitationPurchasingGroupId",
        panel_body_id="panelsolicitationPurchasingGroupId-body",
        hidden_field_id="solicitationPurchasingGroupId",
        view_all_id="viewAllsolicitationPurchasingGroupId",
        default_all=True,
    ),
    Section(
        name="solicitation_types",
        label="Solicitation Type",
        section_key="solicitationType",
        panel_body_id="panelsolicitationType-body",
        hidden_field_id="solicitationType",
        view_all_id="viewAllsolicitationType",
    ),
    Section(
        name="general_requirements",
        label="General Requirements",
        section_key="buyerReqsCodes",
        panel_body_id="panelbuyerReqsCodes-body",
        hidden_field_id="buyerReqsCodes",
        view_all_id="viewAllbuyerReqsCodes",
    ),
]

SECTIONS_BY_NAME: dict[str, Section] = {s.name: s for s in SECTIONS}


# -- status (radio, not a list) ---------------------------------------------

# <input name="status" class="statusRadioButton" type="radio" value="OPEN|CLOSED|AWARD">
STATUS_RADIO_NAME = "status"
STATUS_OPTIONS: list[dict[str, str]] = [
    {"value": "OPEN", "label": "Open Solicitations"},
    {"value": "CLOSED", "label": "Closed Solicitations"},
    {"value": "AWARD", "label": "Awarded Solicitations"},
]
DEFAULT_STATUS = "OPEN"
STATUS_VALUES = {o["value"] for o in STATUS_OPTIONS}


# -- date panels ------------------------------------------------------------

# Both date panels share one markup shape, keyed by the section name below:
#   checkbox   #{section}Check{TYPE}   name="{section}.dateType" value="{TYPE}"
#   the day    #{section}DAY           + hidden #{section}DAY_hidden
#   within     #{section}WITHIN        (select, name="{section}.within")
#   range      #{section}RANGE1/2      + hidden #{section}RANGE1_hidden / RANGE2_hidden
#   apply      #{section}SearchButton      clear #{section}ClearLink
DATE_SECTIONS: dict[str, str] = {
    "published_date": "publishedDate",
    "closing_date": "closingDate",
}

# Date modes each panel offers. Only Published Date carries "Since Last Login";
# the Closing Date panel has no such checkbox.
DATE_TYPES: dict[str, list[dict[str, str]]] = {
    "published_date": [
        {"value": "SINCE_LAST_LOGIN", "label": "Since last login"},
        {"value": "DAY", "label": "On a specific date"},
        {"value": "WITHIN", "label": "Within the last…"},
        {"value": "RANGE", "label": "Between two dates"},
    ],
    "closing_date": [
        {"value": "DAY", "label": "On a specific date"},
        {"value": "WITHIN", "label": "Within the next…"},
        {"value": "RANGE", "label": "Between two dates"},
    ],
}

# <select id="{section}WITHIN" name="{section}.within">
DATE_WITHIN_OPTIONS: list[dict[str, str]] = [
    {"value": "DAY", "label": "One Day"},
    {"value": "WEEK", "label": "One Week"},
    {"value": "MONTH", "label": "One Month"},
    {"value": "YEAR", "label": "One Year"},
]
DATE_WITHIN_VALUES = {o["value"] for o in DATE_WITHIN_OPTIONS}

# jQuery datepicker dateFormat "mm/dd/yy" — i.e. 4-digit year: 08/25/2026.
DATE_INPUT_FORMAT = "%m/%d/%Y"


# -- seeded options ---------------------------------------------------------

def _region_options() -> list[dict[str, str]]:
    """The full Location list, derived from the portal's own id sequence.

    `regionId` runs alphabetically over the 50 states plus District of Columbia
    in a clean arithmetic sequence: ``id = 6 * n + 13`` for the 1-based index n.
    All twelve ids the sidebar ships inline fit it exactly (California 5→43,
    Colorado 6→49, Florida 10→73, Georgia 11→79, Illinois 14→97, Michigan 23→151,
    New Jersey 31→199, New York 33→211, Oklahoma 37→235, Rhode Island 40→253,
    Tennessee 43→271, Texas 44→277), so the whole list is generated rather than
    hand-transcribed. Entries beyond those twelve are *derived, not observed* —
    a discovery pass overwrites them with the portal's real list.
    """
    return [
        {"value": str(6 * index + 13), "label": name}
        for index, name in enumerate(_REGION_NAMES, start=1)
    ]


_REGION_NAMES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "District of Columbia", "Florida", "Georgia",
    "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
    "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire",
    "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota",
    "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
    "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont",
    "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming",
]

# Every purchasing-group id the sidebar's hidden field ships pre-selected. Only
# the twelve rendered inline carry a label; the rest are named by a discovery
# pass. Order is the hidden field's own.
_PURCHASING_GROUP_IDS = [
    "388529351", "388535751", "8405151", "58111501", "388533201", "700137701",
    "12555501", "8408751", "1002527601", "88021351", "845233301", "182875151",
    "388531901", "700141301", "700131701", "8407551", "4213015901", "845225251",
    "88020151", "182878751", "700140101", "8411151", "845232051", "388530651",
    "845227701", "700134101", "93598201", "12556701", "8409951", "845224001",
    "388538251", "182876351", "700132901", "700142501", "845230801", "845226451",
    "388534401", "700136501", "845217101", "8412351", "88022651", "388537001",
    "5611151", "1020597901", "700135301", "388528051", "845229551", "8406351",
    "182877551", "700138901", "5611001", "6605801",
]

_PURCHASING_GROUP_LABELS = {
    "8409951": "Rocky Mountain E-Purchasing System",
    "8411151": "Empire State Purchasing Group",
    "8412351": "MITN Purchasing Group",
    "88020151": "California Purchasing Group",
    "8408751": "Florida Purchasing Group",
    "8407551": "Texas Purchasing Group",
    "8405151": "New Jersey Purchasing Group",
    "388528051": "Tennessee Purchasing Group",
    "58111501": "Georgia Purchasing Group",
    "93598201": "Oklahoma Purchasing Group",
    "700136501": "Rhode Island Purchasing Group",
    "88021351": "Illinois Purchasing Group",
}


def _purchasing_group_options() -> list[dict[str, str]]:
    return [
        {"value": gid, "label": _PURCHASING_GROUP_LABELS.get(gid, f"Purchasing group {gid}")}
        for gid in _PURCHASING_GROUP_IDS
    ]


# Seeded per-section options: what the sidebar renders inline, plus the derived
# Location list and the full pre-selected purchasing-group set. Anything a
# discovery pass finds is merged over the top of these.
OPTIONS: dict[str, list[dict[str, str]]] = {
    "nigp_categories": [
        {"value": "112450", "label": "CONSTRUCTION SERVICES, GENERAL (INCL. MAINTENANCE AND REPAIR SERVICES)"},
        {"value": "112282", "label": "BUILDING CONSTRUCTION SERVICES, NEW (INCL. MAINTENANCE AND REPAIR SERVICES)"},
        {"value": "112716", "label": "CONSULTING SERVICES"},
        {"value": "112774", "label": "Engineering Consulting"},
        {"value": "112520", "label": "Construction, Sewer and Storm Drain"},
        {"value": "112522", "label": "Construction, Sidewalk and Driveway (Includes Pedestrian and Handicap Ramps)"},
        {"value": "112970", "label": "Civil Engineering"},
        {"value": "112580", "label": "Electrical"},
        {"value": "112956", "label": "ENGINEERING SERVICES, PROFESSIONAL"},
        {"value": "112510", "label": "Construction, Highway and Road"},
        {"value": "112296", "label": "Building Construction, Non-Residential (Office Bldg., etc.)"},
    ],
    "organizations": [
        {"value": "443211614109", "label": "The City of Oklahoma City and Trusts"},
        {"value": "43102291752", "label": "City of Miami Beach"},
        {"value": "416971005", "label": "Oakland County"},
        {"value": "415934202", "label": "County of Orange - Department of General Services"},
        {"value": "42832546201", "label": "Seminole Tribe of Florida"},
        {"value": "443248410501", "label": "Knoxville Utilities Board (KUB)"},
        {"value": "43104165502", "label": "Riverbay Corporation"},
        {"value": "417101869", "label": "County of Wayne"},
        {"value": "879177781", "label": "Fulton County Government"},
        {"value": "416610518", "label": "City of Rochester"},
        {"value": "43102522002", "label": "Contra Costa County"},
        {"value": "415323868", "label": "Adams County"},
    ],
    "locations": _region_options(),
    "purchasing_groups": _purchasing_group_options(),
    "solicitation_types": [
        {"value": "RFP_F", "label": "Request for Proposal (Formal)"},
        {"value": "ITB_F", "label": "Invitation to Bid (Formal)"},
        {"value": "RFB", "label": "Request for Bid (Formal)"},
        {"value": "IFB", "label": "Invitation for Bid"},
        {"value": "RFQ_QUAL_F", "label": "Request for Qualifications (Formal)"},
        {"value": "RFQ", "label": "Request for Quote"},
        {"value": "RFQ_FORMAL", "label": "Request for Quote (Formal)"},
        {"value": "RFP", "label": "Request for Proposal"},
        {"value": "RFI_FORMAL", "label": "Request for Information (Formal)"},
        {"value": "RFQ_QUALIF", "label": "Request for Qualifications"},
        {"value": "CSB", "label": "Competitive Sealed Bid"},
        {"value": "ITB_SI", "label": "Invitation to Bid"},
    ],
    "general_requirements": [
        {"value": "INSURANCE_REQUIRED", "label": "Insurance Required"},
        {"value": "ALL_OR_NONE_AWARD", "label": "All or None Award"},
        {"value": "PREVAILING_WAGE_REQUIRED", "label": "Prevailing Wage Required"},
        {"value": "LICENSE_REQUIRED", "label": "License Required"},
        {"value": "CONTRACTOR_LICENSE_REQUIRED", "label": "Contractors License Required"},
        {"value": "FOB_DESTINATION", "label": "FOB Destination"},
        {"value": "BID_DEPOSIT_REQUIRED", "label": "Bid Deposit Required"},
        {"value": "CERTIFIED_PAYROLL", "label": "Certified Payroll"},
        {"value": "WARRANTY_REQUIRED", "label": "Warranty Information Required"},
        {"value": "INSTALLATION_REQUIRED", "label": "Installation Required"},
        {"value": "AWARD_BY_LINE_ITEM", "label": "Reserve Rights to Award by Line Item"},
        {"value": "LOCAL_SERVICE_REQUIRED", "label": "Local Service Required"},
    ],
}

# Sections whose seeded list is only the sidebar's inline top slice — a discovery
# pass is what makes them complete. Location and Purchasing Group are seeded whole
# (derived id sequence / the hidden field's full set), so they are not flagged.
PARTIAL_SECTIONS = {
    "nigp_categories", "organizations", "solicitation_types", "general_requirements",
}


# -- discovered-option cache -------------------------------------------------

def load_options() -> tuple[dict[str, list[dict[str, str]]], str | None]:
    """Seeded options with any discovered ones merged over them.

    Returns ``(options_by_section, discovered_at)``. A discovered section fully
    replaces its seed — the portal's own list is authoritative and complete,
    whereas the seed is a top-12 slice. A missing/unreadable cache degrades to
    the seeds rather than failing the request.
    """
    options = {name: list(values) for name, values in OPTIONS.items()}
    if not OPTIONS_CACHE.exists():
        return options, None
    try:
        cached = json.loads(OPTIONS_CACHE.read_text())
    except (OSError, ValueError):
        logger.warning("bidnet filter option cache unreadable: %s", OPTIONS_CACHE)
        return options, None

    for name, values in (cached.get("options") or {}).items():
        if name in options and values:
            options[name] = values
    return options, cached.get("discovered_at")


def save_discovered(options: dict[str, list[dict[str, str]]], discovered_at: str) -> None:
    """Persist a discovery pass's harvest. Sections that came back empty are
    dropped so a partial pass never blanks a section that was already known."""
    payload = {
        "discovered_at": discovered_at,
        "options": {name: values for name, values in options.items() if values},
    }
    OPTIONS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    OPTIONS_CACHE.write_text(json.dumps(payload, indent=2))


def catalog() -> dict[str, Any]:
    """Everything the frontend needs to render the sidebar filter form."""
    options, discovered_at = load_options()
    return {
        "status": {"options": STATUS_OPTIONS, "default": DEFAULT_STATUS},
        "sections": [
            {
                "name": section.name,
                "label": section.label,
                "section_key": section.section_key,
                "default_all": section.default_all,
                # True while the list is still only the sidebar's inline slice —
                # the UI uses it to offer "refresh from BidNet".
                "partial": section.name in PARTIAL_SECTIONS and not discovered_at,
                "options": options.get(section.name, []),
            }
            for section in SECTIONS
        ],
        "dates": [
            {
                "name": name,
                "label": "Published Date" if name == "published_date" else "Closing Date",
                "types": DATE_TYPES[name],
                "within_options": DATE_WITHIN_OPTIONS,
            }
            for name in DATE_SECTIONS
        ],
        "discovered_at": discovered_at,
    }


# -- request model ----------------------------------------------------------

class DateFilter(BaseModel):
    """One date panel's setting. ``type`` picks the checkbox; the remaining
    fields carry whatever that checkbox's control needs."""

    type: str
    within: str = "DAY"          # WITHIN only
    day: str | None = None       # DAY only — mm/dd/yyyy
    range_start: str | None = None   # RANGE only — mm/dd/yyyy
    range_end: str | None = None     # RANGE only — mm/dd/yyyy

    @field_validator("within")
    @classmethod
    def _known_within(cls, value: str) -> str:
        if value not in DATE_WITHIN_VALUES:
            raise ValueError(f"unknown 'within' period: {value}")
        return value


class SidebarFilterRequest(BaseModel):
    """The frontend's filter choices, as posted to /bidnet/scrape.

    Every list field defaults to "leave this panel alone": an empty list means no
    constraint. Purchasing Group is the exception — the portal ships it fully
    selected, so ``None`` (the default) means "leave all 52 ticked" and an
    explicit list narrows it.
    """

    status: str = DEFAULT_STATUS
    nigp_categories: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    purchasing_groups: list[str] | None = None
    solicitation_types: list[str] = Field(default_factory=list)
    general_requirements: list[str] = Field(default_factory=list)
    published_date: DateFilter | None = None
    closing_date: DateFilter | None = None

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str) -> str:
        if value not in STATUS_VALUES:
            raise ValueError(f"unknown status: {value} (expected one of {sorted(STATUS_VALUES)})")
        return value

    def selection_for(self, section: Section) -> list[str] | None:
        """This request's values for a list section, or None for "don't touch".

        Purchasing Group's None means the portal's own full selection stays as-is;
        every other section's empty list means the panel is left unfiltered.
        """
        chosen = getattr(self, section.name)
        if chosen is None:
            return None
        cleaned = list(dict.fromkeys(v.strip() for v in chosen if v and v.strip()))
        if not cleaned and not section.default_all:
            return None
        return cleaned

    def dates(self) -> list[tuple[str, DateFilter]]:
        """The date panels this request actually sets, as (api name, filter)."""
        return [
            (name, value)
            for name, value in (
                ("published_date", self.published_date),
                ("closing_date", self.closing_date),
            )
            if value is not None
        ]

    def summary(self) -> str:
        """One-line description for the run record / logs."""
        parts = [f"status={self.status}"]
        for section in SECTIONS:
            chosen = self.selection_for(section)
            if chosen is not None:
                parts.append(f"{section.name}={len(chosen)}")
        for name, value in self.dates():
            parts.append(f"{name}={value.type}")
        return ", ".join(parts)


def validate_request(request: SidebarFilterRequest) -> list[str]:
    """Check requested values against the known catalog. Returns human-readable
    problems; an empty list means the request is usable.

    Unknown values are rejected rather than passed through: a typo'd id would
    otherwise silently produce a filter the portal ignores, and a run that
    quietly searched something other than what was asked for is worse than one
    that refuses to start. Date panels are checked for the fields their own type
    requires.
    """
    problems: list[str] = []
    options, _ = load_options()
    for section in SECTIONS:
        chosen = request.selection_for(section)
        if not chosen:
            continue
        known = {o["value"] for o in options.get(section.name, [])}
        unknown = [v for v in chosen if v not in known]
        if unknown:
            problems.append(f"unknown {section.label} value(s): {', '.join(unknown)}")

    for name, value in request.dates():
        known_types = {t["value"] for t in DATE_TYPES[name]}
        if value.type not in known_types:
            problems.append(f"{name}: unknown date type {value.type} (expected {sorted(known_types)})")
            continue
        if value.type == "DAY" and not value.day:
            problems.append(f"{name}: type DAY needs a 'day' date (mm/dd/yyyy)")
        if value.type == "RANGE" and not (value.range_start and value.range_end):
            problems.append(f"{name}: type RANGE needs 'range_start' and 'range_end' (mm/dd/yyyy)")
    return problems
