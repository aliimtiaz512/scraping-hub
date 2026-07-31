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

**Codes are reference, not search terms.** The NIGP/UNSPSC codes from the guide
are recorded in each niche's `notes` for traceability. They are not searched:
the keyword box is full-text, and BidNet's own NIGP sidebar filter keys off
internal ids (`112450`), not published NIGP class-item numbers (`965-46`). Use
the Filters panel to narrow by the portal's NIGP categories.

To change what a run searches, edit `NICHES` here and restart the API.
"""

import logging

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.scrapers.bidnet.niche_models import BidnetNiche, BidnetNicheKeyword

logger = logging.getLogger(__name__)


# key -> {label, slug (used in the run folder name), notes, order, keywords[]}
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
        "keywords": [
            "graphic design",
            "graphic arts",
            "visual identity",
            "brand identity",
            "style guide",
            "rebranding",
            "logo design",
            "desktop publishing",
            "publication layout",
            "technical illustration",
            "prepress",
            "motion graphics",
            "data visualization",
            "ADA compliant",
            "accessible PDF",
            "document accessibility",
            "PDF remediation",
            "WCAG",
            "annual report",
            "public awareness campaign",
            "recreation guide",
            "activity guide",
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
        "keywords": [
            "commercial printing",
            "printing services",
            "offset printing",
            "digital printing",
            "variable data printing",
            "print and mail",
            "direct mail",
            "mailing services",
            "postal presort",
            "kitting",
            "ballot printing",
            "voter guide",
            "bill inserts",
            "wide format",
            "wayfinding signage",
            "vehicle graphics",
            "silk screen",
            "screen printing",
            "banners",
            "decals",
            "perfect bound",
            "saddle stitched",
            "carbonless forms",
            "continuous forms",
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
        "keywords": [
            "custom software development",
            "custom programming",
            "software development services",
            "application development",
            "mobile application development",
            "web portal",
            "portal modernization",
            "citizen self-service",
            "permitting software",
            "licensing software",
            "workflow automation",
            "API integration",
            "systems integration",
            "legacy system modernization",
            "cloud migration",
            "software as a service",
            "software maintenance",
            "software consulting",
            "agile software development",
            "CJIS",
            "SOC 2",
            "web accessibility",
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
        "keywords": [
            "artificial intelligence",
            "machine learning",
            "generative AI",
            "conversational AI",
            "chatbot",
            "large language model",
            "retrieval augmented generation",
            "natural language processing",
            "predictive analytics",
            "predictive modeling",
            "data analytics",
            "big data",
            "computer vision",
            "license plate recognition",
            "anomaly detection",
            "intelligent document processing",
            "optical character recognition",
            "OCR",
            "data extraction",
            "automated data entry",
            "data processing services",
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
        "keywords": [
            "printed circuit board",
            "circuit board assembly",
            "PCBA",
            "bare board",
            "multi-layer PCB",
            "rigid-flex",
            "surface mount",
            "SMT",
            "through hole",
            "conformal coating",
            "IPC-A-610",
            "flying probe",
            "AOI inspection",
            "electronic assembly",
            "electronic components",
            "sensor board",
            "control board",
            "board level repair",
            "electronics repair",
            "SCADA",
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
    """A niche's search terms, in the order the run should work through them.

    Read from the database rather than from `NICHES` so an operator can adjust
    the catalog with SQL between restarts without editing code.
    """
    return list(
        session.execute(
            select(BidnetNicheKeyword.term)
            .where(BidnetNicheKeyword.niche_key == key)
            .order_by(BidnetNicheKeyword.sort_order, BidnetNicheKeyword.id)
        ).scalars()
    )


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
            terms = list(dict.fromkeys(
                term.strip() for term in (entry.get("keywords") or []) if term and term.strip()
            ))
            session.add_all(
                BidnetNicheKeyword(niche_key=key, term=term, sort_order=order)
                for order, term in enumerate(terms)
            )

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
            "bidnet catalog: seeded %d niche(s), %d keyword(s)",
            len(NICHES),
            sum(len(n.get("keywords") or []) for n in NICHES.values()),
        )
    except Exception:  # noqa: BLE001 — seeding must never block API startup
        session.rollback()
        logger.exception("bidnet catalog: seeding failed — the niche dropdown may be stale or empty")
    finally:
        session.close()
