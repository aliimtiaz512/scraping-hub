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
            "965-46",
            "915-48",
            "915-22",
            "915-09",
            "82131603",
        ],
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
        # Searched as text in the same box as the keywords, after them; the
        # `notes` above say what each code covers.
        "nigp_codes": [
            "966-00",
            "966-18",
            "966-28",
            "966-55",
            "966-86",
        ],
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
        # Searched as text in the same box as the keywords, after them; the
        # `notes` above say what each code covers.
        "nigp_codes": [
            "920-40",
            "920-45",
            "920-03",
            "918-29",
            "81111500",
        ],
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
        # Searched as text in the same box as the keywords, after them; the
        # `notes` above say what each code covers.
        "nigp_codes": [
            "920-04",
            "918-30",
            "920-24",
            "81111508",
        ],
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
        # Searched as text in the same box as the keywords, after them; the
        # `notes` above say what each code covers.
        "nigp_codes": [
            "287-54",
            "287-00",
            "936-25",
            "32101501",
        ],
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
