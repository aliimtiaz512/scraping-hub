"""BidNet Direct niche catalog — the source of truth for what a run searches.

Derived from the client's *BidNet Direct (SOVRA) Procurement Taxonomy Guide*,
which maps five business sectors to their SLED procurement language. Each niche
owns a flat list of search keywords; `seed_niches()` materialises this file into
`bidnet_niches` / `bidnet_niche_keywords` at API startup, and the scraper reads
the tables.

**One keyword, one search.** The taxonomy guide offers "copy-paste search
strings" of the form::

    ("graphic design" OR "ADA compliant") AND ("annual report" OR "signage")

Those are deliberately *not* used. A combined boolean query only returns
solicitations matching every AND-group, which is a small fraction of what the
individual terms find. The guide's strings are decomposed here into their
component terms, and the scraper searches each one separately in the same
session, merging and de-duplicating the results.

**No quoting.** Terms are stored bare. Quoting was tried first (the taxonomy
guide quotes every phrase in its examples), but a live comparison showed the
quotes are inert — BidNet returns identical counts either way:

    printed circuit board      0     "printed circuit board"      0
    graphic design             5     "graphic design"             5
    construction             525     "construction"             525
    machine learning           0     "machine learning"           0

So they added noise to the logs and the `Matched Keyword` column without
changing a single result, and were removed.

**Expect many terms to match nothing on a given day.** In the run above only one
of four had any Member Agency bids. That is normal, not a fault — the scraper
detects a zero-result search from the portal's own count and moves straight to
the next keyword (see `BidnetScraper.result_count`).

**No tiers.** Every keyword in a niche is searched; there is no core/extended
split (the previous catalog had one, and it only fragmented the output folders).

**Codes are searched too, as text.** Each niche owns a second list,
`nigp_codes` — the NIGP class-item and UNSPSC numbers from the guide — and the
run searches them through the *same* box, one at a time, after its keywords.
Agencies routinely put the code in the notice itself ("NIGP 965-46", "Commodity
Code 966-18"), so the box finds them; a term that matches nothing costs one
search, the same as any keyword that misses.

This is **not** the same thing as BidNet's NIGP sidebar filter, and neither
replaces the other. That filter keys off the portal's own internal ids
(`112450`), not published class-item numbers (`965-46`), and narrows by how the
*portal* classified a solicitation. Searching the code as text finds the notices
that quote it in their own words. Use the Filters panel for the former; this list
is the latter.

To change what a run searches, edit `NICHES` here and restart the API.

**Seeded 2026-08-20 from the client's "nigps and keywords of bidnet" document**:
469 keywords and 274 NIGP codes across the five niches, replacing the 108 + 23
that were cleared beforehand. Keywords are stored lowercased and whitespace-
collapsed, as the document wrote them; codes are the five-digit class-item form
it uses.

**The codes here are five digits, and their leading zeros are real.** `03752` is
class 037, item 52 — novelties and advertising specialty — and `01500` is class
015. Padding a truncated code back to five is therefore correct for this list,
unlike the NAICS codes elsewhere in the hub, where nothing starts with a zero
and padding invents a code. An earlier version of this catalog wrote the same
kind of code hyphenated (`965-46`); the document uses the unhyphenated form and
that is what is stored, because it is what the client searches on.

**A full run is now 743 searches, up from 131.** Every term is one search of the
portal, so the sweep takes roughly five to six times as long as it did — see the
per-niche counts in the run log. That is the cost of the wider list, not a
fault, but it is worth knowing before a batch is started.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.exc import DatabaseError

from app.db import SessionLocal
from app.scrapers.bidnet.niche_models import BidnetNiche, BidnetNicheKeyword

logger = logging.getLogger(__name__)

# What a stored term is. Both go into the same search box, one at a time; the
# kind exists so a run can say which of the two produced a bid, and so the
# catalog can keep them in a defined order (every keyword, then every code).
KIND_KEYWORD = "keyword"
KIND_NIGP = "nigp"


@dataclass(frozen=True)
class SearchTerm:
    """One thing to type into BidNet's search box, and what kind of thing it is."""

    term: str
    kind: str = KIND_KEYWORD

    @property
    def label(self) -> str:
        """How the run's logs name this kind of search."""
        return "NIGP CODE" if self.kind == KIND_NIGP else "KEYWORD"

    def __str__(self) -> str:      # what lands in the `Matched Keyword` column
        return self.term


# key -> {label, slug (used in the run folder name), notes, order, keywords[],
#         nigp_codes[]}
NICHES: dict[str, dict] = {
    "graphic_design": {
        "label": "Graphic Design & Visual Communication",
        "slug": "Graphic-Design",
        "order": 1,
        "notes": (
            "NIGP 965-46 Graphic Design Services, 915-48 Graphic Arts, "
            "915-22 Communications & Marketing, 915-09 Audio & Video Production; "
            "UNSPSC 82131603. Mandatory SLED standard: WCAG 2.1 AA / state ADA."
        ),
        # Searched as text in the same box as the keywords, after them; the
        # `notes` above say what each code covers.
        "nigp_codes": [
            "03752",
            "05000",
            "08000",
            "35000",
            "80100",
            "88000",
            "90640",
            "90652",
            "90735",
            "90738",
            "91500",
            "91501",
            "91504",
            "91506",
            "91507",
            "91509",
            "91522",
            "91523",
            "91542",
            "91548",
            "91551",
            "91552",
            "91564",
            "91571",
            "91572",
            "91573",
            "91574",
            "91578",
            "91582",
            "91584",
            "91590",
            "91596",
            "91807",
            "91826",
            "91876",
            "92026",
            "96104",
            "96153",
            "96166",
            "96186",
            "96190",
            "96214",
            "96248",
            "96279",
            "96546",
        ],
        "keywords": [
            "accessibility remediation",
            "advertising specialty",
            "agency of record",
            "animation",
            "annual report design",
            "aor",
            "artwork",
            "artwork and design services",
            "banner design",
            "bcc material",
            "brand guidelines",
            "brand identity",
            "brand standards",
            "branding",
            "brochure design",
            "camera ready art",
            "campaign development",
            "collateral development",
            "communications framework",
            "communications plan",
            "corporate identity",
            "cpv 79340000",
            "cpv 79415200",
            "cpv 79822500",
            "creative agency",
            "creative services",
            "data visualization",
            "design and print framework",
            "design services",
            "design system",
            "desktop publishing",
            "digital accessibility",
            "display design",
            "empanelment of creative agency",
            "environmental graphics",
            "exhibit design",
            "graphic arts",
            "graphic design",
            "graphic design consultancy",
            "graphic design services",
            "human centered design",
            "iec material",
            "illustration",
            "infographic",
            "integrated marketing",
            "layout and design",
            "logo design",
            "marketing and outreach",
            "marketing collateral",
            "media buying",
            "media planning",
            "motion graphics",
            "multilingual materials",
            "newsletter design",
            "photography services",
            "poster design",
            "promotional items",
            "promotional products",
            "prototype",
            "public awareness campaign",
            "public information officer",
            "public outreach",
            "rebrand",
            "responsive design",
            "section 508",
            "service design",
            "signage design",
            "social media graphics",
            "storyboard",
            "style guide",
            "trade show booth",
            "typesetting",
            "ui design",
            "ui/ux",
            "unspsc 82141500",
            "user experience",
            "user interface",
            "ux design",
            "vehicle wrap",
            "video production",
            "videography",
            "visual identity",
            "wayfinding",
            "wcag",
            "web design",
            "website redesign",
            "wireframe",
        ],
    },
    "commercial_printing": {
        "label": "Commercial Printing, Publishing & Media Packaging",
        "slug": "Commercial-Printing",
        "order": 2,
        "notes": (
            "NIGP 966-00 Printing & Related Services, 966-18 Offset, 966-28 Digital, "
            "966-55 Mailing & Kitting, 966-86 Silk Screen & Specialty; "
            "UNSPSC 82121500. SLED standard: postal presort / green ink laws."
        ),
        # Searched as text in the same box as the keywords, after them; the
        # `notes` above say what each code covers.
        "nigp_codes": [
            "01500",
            "12500",
            "25500",
            "31000",
            "39500",
            "60000",
            "64500",
            "70000",
            "71500",
            "86000",
            "90800",
            "91500",
            "91544",
            "91557",
            "91558",
            "91568",
            "91778",
            "93660",
            "93927",
            "96227",
            "96233",
            "96251",
            "96253",
            "96278",
            "96500",
            "96600",
            "96603",
            "96605",
            "96607",
            "96611",
            "96613",
            "96616",
            "96618",
            "96622",
            "96625",
            "96627",
            "96628",
            "96631",
            "96636",
            "96642",
            "96646",
            "96651",
            "96652",
            "96655",
            "96657",
            "96658",
            "96659",
            "96660",
            "96661",
            "96662",
            "96663",
            "96664",
            "96665",
            "96666",
            "96667",
            "96668",
            "96669",
            "96670",
            "96671",
            "96672",
            "96673",
            "96674",
            "96675",
            "96676",
            "96678",
            "96681",
            "96684",
            "96685",
            "96686",
            "96689",
            "96690",
            "96692",
            "96693",
            "96694",
            "96695",
            "96700",
            "98564",
        ],
        "keywords": [
            "annual report printing",
            "ballot printing",
            "ballots",
            "banner printing",
            "bid document reproduction",
            "bindery",
            "blueprinting",
            "book printing",
            "brochures",
            "business cards",
            "carbonless forms",
            "catalog printing",
            "check printing",
            "cheque printing",
            "cmyk",
            "coil binding",
            "commercial printing",
            "continuous forms",
            "cpv 22000000",
            "cpv 79800000",
            "cpv 79810000",
            "decals",
            "die cutting",
            "digital printing",
            "direct mail",
            "door hanger",
            "eddm",
            "election printing",
            "embossing",
            "envelope printing",
            "flexography",
            "foil stamping",
            "forms printing",
            "four color process",
            "fsc certified paper",
            "fulfillment services",
            "grand format",
            "inserting and collating",
            "laminating",
            "large format printing",
            "letterhead",
            "letterpress",
            "lettershop",
            "mail house",
            "managed print services",
            "micr",
            "mps",
            "ncr forms",
            "newsletters",
            "offset printing",
            "pad printing",
            "pantone",
            "parking permits",
            "perfect bound",
            "placards",
            "plan reproduction",
            "plotting services",
            "pod printing",
            "prepress",
            "presort",
            "print and mail",
            "print management services",
            "print on demand",
            "print services",
            "print to mail",
            "printing and binding",
            "printing and stationery",
            "printing and supply of",
            "printing services",
            "printing works",
            "proofing",
            "recycled stock",
            "remittance processing",
            "reprographics",
            "saddle stitch",
            "screen printing",
            "secure document printing",
            "security printing",
            "silk screen",
            "snap-out forms",
            "statement printing",
            "stationery",
            "supply of printed material",
            "tamper evident",
            "tax bill printing",
            "textbook printing",
            "thermography",
            "tickets and coupon books",
            "unspsc 82121500",
            "utility bill printing",
            "variable data printing",
            "vdp",
            "vote by mail",
            "wide format",
            "wire-o binding",
            "wristbands",
        ],
    },
    "software_development": {
        "label": "Custom Software Development, Cloud & Enterprise IT",
        "slug": "Software-Development",
        "order": 3,
        "notes": (
            "NIGP 920-40 Custom Programming, 920-45 Software Maintenance & Support, "
            "920-03 Application Service Provider, 918-29 Computer Software Consulting; "
            "UNSPSC 81111500. SLED standards: CJIS / SOC 2 Type II / WCAG 2.1 AA."
        ),
        # Searched as text in the same box as the keywords, after them; the
        # `notes` above say what each code covers.
        "nigp_codes": [
            "20400",
            "20429",
            "20464",
            "20600",
            "20654",
            "20700",
            "20800",
            "20811",
            "20821",
            "20900",
            "88300",
            "91526",
            "91551",
            "91596",
            "91800",
            "91820",
            "91821",
            "91828",
            "91829",
            "91830",
            "91835",
            "91875",
            "91883",
            "91888",
            "91893",
            "91895",
            "92000",
            "92002",
            "92003",
            "92004",
            "92005",
            "92007",
            "92014",
            "92015",
            "92016",
            "92018",
            "92019",
            "92020",
            "92021",
            "92022",
            "92023",
            "92024",
            "92025",
            "92026",
            "92027",
            "92028",
            "92029",
            "92031",
            "92032",
            "92033",
            "92034",
            "92035",
            "92037",
            "92038",
            "92040",
            "92042",
            "92043",
            "92044",
            "92045",
            "92046",
            "92047",
            "92049",
            "92056",
            "92063",
            "92064",
            "92065",
            "92075",
            "92076",
            "92077",
            "92084",
            "92090",
            "92091",
            "92094",
            "92400",
            "93921",
            "95823",
            "95882",
            "96156",
            "96258",
            "96269",
            "96400",
            "98400",
            "99022",
        ],
        "keywords": [
            "311 system",
            "agile development",
            "amc",
            "annual maintenance contract",
            "api development",
            "api integration",
            "application development",
            "application modernization",
            "arcgis",
            "asset management system",
            "bespoke software",
            "business intelligence",
            "ci/cd",
            "cloud hosting",
            "cloud migration",
            "cmms",
            "computerized maintenance management system",
            "constituent relationship management",
            "content management system",
            "cots",
            "cpv 48000000",
            "cpv 72000000",
            "cpv 72212000",
            "crm",
            "crown commercial service",
            "custom software development",
            "cybersecurity services",
            "dashboards and reporting",
            "data governance",
            "data lake",
            "data warehouse",
            "dbits",
            "deliverables based it services",
            "devops",
            "devsecops",
            "digital outcomes and specialists",
            "digital transformation",
            "document management system",
            "drupal",
            "e-governance",
            "e-government",
            "e-permitting",
            "eam",
            "electronic content management",
            "enterprise application",
            "enterprise asset management",
            "erp",
            "erp implementation",
            "esri",
            "etl",
            "expression of interest",
            "fedramp",
            "financial system replacement",
            "g-cloud",
            "gis services",
            "help desk",
            "hris",
            "ict services",
            "identity and access management",
            "independent verification and validation",
            "invitation to tender",
            "it staffing",
            "itsac",
            "itsm",
            "iv&v",
            "land management system",
            "legacy modernization",
            "legacy system replacement",
            "low code",
            "managed services",
            "master data management",
            "microservices",
            "microsoft dynamics",
            "middleware",
            "mis development",
            "mobile application development",
            "no code",
            "payroll system",
            "penetration testing",
            "permitting and licensing software",
            "portal development",
            "power platform",
            "qcbs",
            "quality assurance testing",
            "records management system",
            "replatforming",
            "saas",
            "salesforce",
            "scrum",
            "service desk",
            "servicenow",
            "sharepoint",
            "siem",
            "single sign on",
            "sitc",
            "software as a service",
            "software development",
            "software engineering",
            "software house",
            "software maintenance and support",
            "source code escrow",
            "staff augmentation",
            "stateramp",
            "supply installation testing and commissioning",
            "systems integration",
            "systems integrator",
            "turnkey solution",
            "tx-ramp",
            "user acceptance testing",
            "vulnerability assessment",
            "web application",
            "website development and hosting",
            "wordpress",
            "work order management",
            "workflow automation",
            "zero trust",
        ],
    },
    "ai_analytics": {
        "label": "Artificial Intelligence, Machine Learning & Analytics",
        "slug": "AI-Analytics",
        "order": 4,
        "notes": (
            "NIGP 920-04 AI & Machine Learning Services, 918-30 Computer Network / "
            "Data Consulting, 920-24 Data Processing & Capture; UNSPSC 81111508. "
            "SLED standard: state data privacy standards."
        ),
        # Searched as text in the same box as the keywords, after them; the
        # `notes` above say what each code covers.
        "nigp_codes": [
            "20429",
            "20800",
            "20900",
            "22000",
            "55000",
            "68087",
            "88300",
            "91829",
            "91830",
            "91838",
            "91875",
            "91888",
            "92004",
            "92005",
            "92007",
            "92015",
            "92016",
            "92022",
            "92024",
            "92032",
            "92033",
            "92038",
            "92040",
            "92045",
            "92064",
            "92076",
            "92400",
            "94677",
            "95882",
            "96156",
            "96258",
            "99080",
        ],
        "keywords": [
            "adaptive traffic signal",
            "agentic ai",
            "ai agent",
            "ai and machine learning services",
            "ai framework agreement",
            "ai governance",
            "ai literacy training",
            "ai pilot",
            "ai readiness assessment",
            "ai sandbox",
            "ai strategy and roadmap",
            "algorithmic accountability",
            "algorithmic bias audit",
            "alpr",
            "amazon bedrock",
            "anomaly detection",
            "anpr",
            "artificial intelligence",
            "automatic speech recognition",
            "azure openai",
            "biometrics",
            "chatbot",
            "computer vision",
            "conversational ai",
            "copilot",
            "cpv 72212900",
            "deep learning",
            "digital public infrastructure",
            "digital twin",
            "emerging technologies",
            "facial recognition",
            "foundation model",
            "fraud detection",
            "genai",
            "generative ai",
            "govtech",
            "hyperautomation",
            "image recognition",
            "innovation partnership",
            "intelligent automation",
            "intelligent document processing",
            "iot analytics",
            "large language model",
            "license plate recognition",
            "lidar classification",
            "llm",
            "machine learning",
            "mlops",
            "model governance",
            "national ai strategy",
            "natural language processing",
            "neural network",
            "nlp",
            "object detection",
            "ocr",
            "optical character recognition",
            "predictive analytics",
            "predictive modeling",
            "prompt engineering",
            "proof of concept",
            "recommendation engine",
            "responsible ai",
            "retrieval augmented generation",
            "risk scoring",
            "robotic process automation",
            "rpa",
            "sensor analytics",
            "smart city",
            "speech to text",
            "text to speech",
            "vertex ai",
            "video analytics",
            "virtual agent",
            "virtual assistant",
        ],
    },
    "pcb_electronics": {
        "label": "Printed Circuit Board (PCB) Electronics & Assemblies",
        "slug": "PCB-Electronics",
        "order": 5,
        "notes": (
            "NIGP 287-54 Printed Circuit Boards, 287-00 Electronic Components & "
            "Accessories, 936-25 Electrical Equipment Maintenance; UNSPSC 32101501. "
            "SLED standards: IPC-A-610 / ISO 9001."
        ),
        # Searched as text in the same box as the keywords, after them; the
        # `notes` above say what each code covers.
        "nigp_codes": [
            "20416",
            "20464",
            "22000",
            "25700",
            "28000",
            "28500",
            "28700",
            "54500",
            "55300",
            "57800",
            "59300",
            "69000",
            "69100",
            "72500",
            "72600",
            "73000",
            "80300",
            "84000",
            "84500",
            "86400",
            "88300",
            "90626",
            "91828",
            "92522",
            "92531",
            "92532",
            "92557",
            "92565",
            "93625",
            "93937",
            "93973",
            "95944",
            "96238",
            "96245",
            "96700",
            "99240",
            "99255",
        ],
        "keywords": [
            "altium",
            "aoi",
            "asic",
            "automated optical inspection",
            "backplane",
            "bare board",
            "bill of materials",
            "board assembly",
            "board fabrication",
            "board level repair",
            "box build",
            "burn-in testing",
            "cable assembly",
            "capacitors and resistors",
            "cca",
            "circuit card assembly",
            "conformal coating",
            "contract manufacturing",
            "control board replacement",
            "controller board",
            "counterfeit parts avoidance",
            "cpv 31711000",
            "cpv 31712000",
            "depot repair",
            "design and development of electronic hardware",
            "design for manufacturability",
            "dfars",
            "discrete components",
            "electronic assembly",
            "electronic hardware design",
            "electronics manufacturing services",
            "embedded systems",
            "ems provider",
            "end of life components",
            "esd control",
            "fabrication of pcb",
            "firmware development",
            "flex pcb",
            "flexible circuit",
            "flying probe",
            "fpga",
            "fr-4",
            "functional test fixture",
            "gerber files",
            "hdi",
            "hs 8534",
            "impedance control",
            "in-circuit test",
            "instrumentation and calibration",
            "integrated circuit",
            "ipc class 3",
            "ipc-6012",
            "ipc-a-610",
            "itar",
            "j-std-001",
            "kicad",
            "led board",
            "microcontroller",
            "multilayer board",
            "new product introduction",
            "obsolescence management",
            "odb++",
            "orcad",
            "panelization",
            "pcb",
            "pcb layout",
            "pcba",
            "plated through hole",
            "plc control panel",
            "power supply board",
            "printed circuit board",
            "printed wiring board",
            "prototyping and low volume production",
            "pwb",
            "reach compliance",
            "reflow soldering",
            "refurbishment of electronic boards",
            "relays and transformers",
            "rigid-flex",
            "rohs",
            "scada rtu",
            "schematic capture",
            "semiconductor",
            "single board computer",
            "smt",
            "solder paste stencil",
            "supply of electronic components",
            "surface mount technology",
            "telemetry board",
            "through hole",
            "trusted supplier",
            "turnkey assembly",
            "unspsc 32101500",
            "wave soldering",
            "wire harness",
            "x-ray inspection",
        ],
    },
}


# -- reads ------------------------------------------------------------------

def list_niches(session) -> list[BidnetNiche]:
    """Active niches in catalog order, for the dropdown."""
    return list(
        session.execute(
            select(BidnetNiche)
            .where(BidnetNiche.is_active.is_(True))
            .order_by(BidnetNiche.sort_order, BidnetNiche.key)
        ).scalars()
    )


def get_niche(session, key: str) -> BidnetNiche | None:
    niche = session.get(BidnetNiche, key)
    return niche if niche is not None and niche.is_active else None


def keywords_for(session, key: str) -> list[str]:
    """A niche's keyword terms — the codes are `nigp_codes_for`.

    Read from the database rather than from `NICHES` so an operator can adjust
    the catalog with SQL between restarts without editing code.
    """
    return [term.term for term in search_terms_for(session, key) if term.kind == KIND_KEYWORD]


def nigp_codes_for(session, key: str) -> list[str]:
    """A niche's NIGP/UNSPSC codes, searched as text after its keywords."""
    return [term.term for term in search_terms_for(session, key) if term.kind == KIND_NIGP]


def _is_missing_kind_column(exc: Exception) -> bool:
    """Is this the one error the file fallback exists for — `kind` not there yet?

    Matched on the message because the two databases in play say it differently
    and neither exposes it as a distinct exception class: Postgres raises
    `ProgrammingError: column bidnet_niche_keywords.kind does not exist`, SQLite
    an `OperationalError: no such column: …kind`. Anything else — a dropped
    connection above all — must not be mistaken for it.
    """
    message = str(exc).lower()
    return "kind" in message and (
        "does not exist" in message or "no such column" in message
        or "unknown column" in message or "undefinedcolumn" in message
    )


def search_terms_for(session, key: str) -> list[SearchTerm]:
    """Everything a run of this niche types into the search box, in order.

    Keywords first, then NIGP codes — the order the rows were seeded in, which
    is also the order the run works through them. One list rather than two so
    the scraper has a single queue to iterate and a single place that knows
    which kind each term is.

    Falls back to the catalog file if the `kind` column is not there yet (a
    database that predates the codes and has not had
    `migrations/2026-08-11_add_bidnet_niche_kind.sql` applied). Falling back
    beats failing the run: the file is the source of truth the table is seeded
    from, so its terms are the same ones — an operator's SQL edits are what get
    missed, and the log says so.
    """
    try:
        rows = session.execute(
            select(BidnetNicheKeyword.term, BidnetNicheKeyword.kind)
            .where(BidnetNicheKeyword.niche_key == key)
            .order_by(BidnetNicheKeyword.sort_order, BidnetNicheKeyword.id)
        ).all()
    except DatabaseError as exc:
        if not _is_missing_kind_column(exc):
            # A database that is down, or any other failure, is not this
            # function's to absorb: the router turns it into a 503 and the run
            # fails loudly. Only the one recoverable shape falls through.
            raise
        session.rollback()
        entry = NICHES.get(key) or {}
        logger.warning(
            "bidnet catalog: bidnet_niche_keywords has no `kind` column — reading "
            "niche %r from the catalog file instead. Apply "
            "migrations/2026-08-11_add_bidnet_niche_kind.sql to restore database "
            "reads (any SQL edits to the catalog are being ignored until then).",
            key,
        )
        return [
            SearchTerm(term, kind)
            for kind, field in ((KIND_KEYWORD, "keywords"), (KIND_NIGP, "nigp_codes"))
            for term in (entry.get(field) or [])
        ]
    return [SearchTerm(term, kind or KIND_KEYWORD) for term, kind in rows]


# -- seeding ----------------------------------------------------------------

def seed_niches() -> None:
    """Load `NICHES` into the catalog tables. Called once at startup.

    Best-effort by design: this runs inside `init_db`, and a malformed catalog
    (or a database that is simply down) must not stop the API from serving every
    other portal. Failures are logged and swallowed.
    """
    session = SessionLocal()
    try:
        for key, entry in NICHES.items():
            niche = session.get(BidnetNiche, key)
            if niche is None:
                niche = BidnetNiche(key=key)
                session.add(niche)
            niche.label = str(entry.get("label") or key)
            niche.slug = entry.get("slug") or key
            niche.notes = entry.get("notes") or None
            niche.sort_order = int(entry.get("order") or 0)
            niche.is_active = True

            # Replace this niche's terms wholesale — the file is authoritative,
            # so a term deleted there must disappear here too.
            session.execute(
                delete(BidnetNicheKeyword).where(BidnetNicheKeyword.niche_key == key)
            )
            session.flush()
            # Keywords first, then the NIGP codes — `sort_order` is what the run
            # iterates in, so this is what puts every keyword search ahead of
            # every code search. Deduplicated across *both* lists: the same
            # string in each would otherwise breach the (niche_key, term) unique
            # constraint and cost the whole niche its terms.
            seen: set[str] = set()
            rows: list[BidnetNicheKeyword] = []
            for kind, field in ((KIND_KEYWORD, "keywords"), (KIND_NIGP, "nigp_codes")):
                for term in entry.get(field) or []:
                    term = (term or "").strip()
                    if not term or term in seen:
                        continue
                    seen.add(term)
                    rows.append(
                        BidnetNicheKeyword(
                            niche_key=key, term=term, kind=kind, sort_order=len(rows)
                        )
                    )
            session.add_all(rows)

        # A niche dropped from the file is retired, not deleted: scraped bids
        # still name it, and deleting would cascade them away.
        retired = session.execute(
            select(BidnetNiche).where(BidnetNiche.key.notin_(list(NICHES)))
        ).scalars().all()
        for niche in retired:
            if niche.is_active:
                logger.info("bidnet catalog: niche %r no longer in the file — deactivating", niche.key)
            niche.is_active = False

        session.commit()
        logger.info(
            "bidnet catalog: seeded %d niche(s), %d keyword(s), %d NIGP code(s)",
            len(NICHES),
            sum(len(n.get("keywords") or []) for n in NICHES.values()),
            sum(len(n.get("nigp_codes") or []) for n in NICHES.values()),
        )
    except Exception as exc:  # noqa: BLE001 — seeding must never block API startup
        session.rollback()
        if _is_missing_kind_column(exc):
            # A pending migration is an instruction, not a mystery. Said in one
            # line, at WARNING, because the stack trace under it answers a
            # question nobody has: the fix is a file, and it is named here.
            logger.warning(
                "bidnet catalog: NOT seeded — bidnet_niche_keywords has no `kind` "
                "column, so the niches' NIGP codes cannot be stored. Run:\n"
                "    psql \"$DATABASE_URL\" -f server/migrations/"
                "2026-08-11_add_bidnet_niche_kind.sql\n"
                "and restart the API. Until then the catalog keeps whatever terms "
                "it already held, and runs read their terms from niches.py.",
            )
            return
        logger.exception("bidnet catalog: seeding failed — the niche dropdown may be stale or empty")
    finally:
        session.close()
