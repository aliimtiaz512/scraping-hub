"""The SEPTA niche catalog — keywords and commodity codes, grouped by niche.

This file is the **source of truth**. `seed_niches()` runs at startup (from
`app.db.init_db`) and materialises it into `septa_niches` /
`septa_niche_keywords` / `septa_niche_codes`, which is what the API serves to
the dropdown and what the scraper queries to resolve a run's search terms.

To change the catalog: edit `NICHES` below and restart the API. There is no
admin UI by design — same approach as `app/scrapers/bidnet/keywords.py`.

Shape of an entry
-----------------
    "niche_key": {
        "label": "Human readable name",     # shown in the dropdown
        "slug":  "Filename-Safe-Name",      # used in file/sheet names
        "order": 1,                         # dropdown sort order
        "keywords": [
            {"term": "Artificial Intelligence", "tier": "core", "notes": "..."},
        ],
        "codes": [
            {"code": "9204", "title": "Data Processing Services", "notes": "..."},
        ],
    }

`tier` and `notes` are optional and purely informational — every keyword and
every code in a niche is searched, one search per term (never concatenated).
The scraper runs N keyword searches + M code searches, then merges and
deduplicates by requisition number.

Seeding semantics
-----------------
* A niche is matched by its key; its keywords and codes are replaced wholesale
  on every startup, so deleting a term from this file removes it from the DB.
* A niche that disappears from this file is marked `is_active = false` rather
  than deleted — quotes scraped under it still reference it by name.
"""

import logging
from typing import Any

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.scrapers.septa.niche_models import SeptaNiche, SeptaNicheCode, SeptaNicheKeyword

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# THE CATALOG
#
# Source: "SEPTA ePS Bid Search Checklist — Graphic Design · Digital Marketing ·
# Print · Software Development · AI" (client-supplied). Keywords are transcribed
# verbatim from §3 of that document, including its per-term notes.
#
# The checklist states the search has no boolean operators and that keywords must
# be run one at a time — which is exactly what the scraper does (one search per
# term, results merged and deduplicated).
#
# ⚠ COMMODITY CODES ARE UNVERIFIED. §4 of the checklist gives NIGP *category
# ranges* (915 / 918 / 920 / 958), not exact codes, and says outright that "the
# exact dropdown options are populated live on the ePS page and could not be
# extracted remotely — confirm exact labels directly on the site." They are
# seeded here so the code path is live, but until someone reads the real
# dropdown these searches may match nothing. A code search that returns nothing
# is harmless (the run logs it and moves on), so the keywords still carry the
# result set. Replace them with the real codes once confirmed.
# ---------------------------------------------------------------------------
NICHES: dict[str, dict[str, Any]] = {
    "graphic_design_print": {
        "label": "Graphic Design & Print",
        "slug": "Graphic-Design-Print",
        "order": 1,
        "keywords": [
            {"term": "graphic design", "tier": "core", "notes": "Primary term."},
            {"term": "print services", "tier": "core", "notes": "Checklist pairs this with 'printing services'."},
            {"term": "printing services", "tier": "core", "notes": "Alternate phrasing of 'print services'."},
            {"term": "print", "tier": "extended", "notes": "Singular form the checklist calls out explicitly."},
            {"term": "creative services", "tier": "core", "notes": "Broader — may catch bundled RFPs."},
            {"term": "design and print", "tier": "core", "notes": "Common combined listing title."},
            {"term": "signage", "tier": "core", "notes": "SEPTA posts signage/wayfinding work separately."},
            {"term": "publication design", "tier": "extended", "notes": "Relevant if targeting print collateral."},
        ],
        "codes": [
            {"code": "920", "title": "Printing, Reproduction, Photography",
             "notes": "UNVERIFIED category range — confirm the exact ePS dropdown code."},
            {"code": "918", "title": "Design and Layout Services",
             "notes": "UNVERIFIED — checklist groups this under printing or professional services."},
        ],
    },
    "digital_marketing": {
        "label": "Digital Marketing",
        "slug": "Digital-Marketing",
        "order": 2,
        "keywords": [
            {"term": "digital marketing", "tier": "core", "notes": "Primary term."},
            {"term": "marketing services", "tier": "core", "notes": "Broader catch-all."},
            {"term": "social media", "tier": "core", "notes": "Often bundled into outreach contracts."},
            {"term": "advertising services", "tier": "core", "notes": "Separate commodity area."},
            {"term": "outreach services", "tier": "core",
             "notes": "SEPTA frequently posts rider/community 'Outreach Services' RFPs — check even without 'marketing' in the title."},
            {"term": "public relations", "tier": "extended", "notes": "Related adjacent category."},
        ],
        "codes": [
            {"code": "915", "title": "Advertising, Public Relations, Marketing",
             "notes": "UNVERIFIED category range — confirm the exact ePS dropdown code."},
        ],
    },
    "software_development": {
        "label": "Software Development",
        "slug": "Software-Development",
        "order": 3,
        "keywords": [
            {"term": "software development", "tier": "core", "notes": "Primary term."},
            {"term": "application development", "tier": "core", "notes": "Alternate phrasing."},
            {"term": "IT services", "tier": "extended", "notes": "Broad — high noise, still worth a scan."},
            {"term": "systems integration", "tier": "core", "notes": "Larger-scale software contracts."},
            {"term": "custom software", "tier": "extended", "notes": "Less common phrasing, still worth trying."},
            {"term": "web development", "tier": "core", "notes": "Website/portal-specific work."},
        ],
        "codes": [
            {"code": "958", "title": "Data Processing, Computer Services",
             "notes": "UNVERIFIED category range — confirm the exact ePS dropdown code."},
            {"code": "920", "title": "Software, Programming, Data Processing",
             "notes": "UNVERIFIED — checklist lists this as '920 / 958'."},
        ],
    },
    "ai": {
        "label": "AI & Analytics",
        "slug": "AI-Analytics",
        "order": 4,
        "keywords": [
            {"term": "artificial intelligence", "tier": "core", "notes": "Primary term."},
            {"term": "machine learning", "tier": "core", "notes": "Alternate phrasing."},
            {"term": "AI", "tier": "extended", "notes": "Standalone search — may return unrelated noise."},
            {"term": "automation", "tier": "extended", "notes": "Related adjacent category."},
            {"term": "predictive analytics", "tier": "core", "notes": "Related adjacent category."},
            {"term": "data analytics", "tier": "core",
             "notes": "AI work is often bucketed here rather than under a dedicated 'AI' listing."},
        ],
        "codes": [
            {"code": "958", "title": "Data Processing, Computer Services",
             "notes": "UNVERIFIED — checklist notes AI rarely has its own dedicated code yet."},
        ],
    },
}


def _keyword_rows(niche_key: str, entries: list[dict[str, Any]]) -> list[SeptaNicheKeyword]:
    """Build keyword rows, dropping blanks and duplicates within the niche.

    The unique constraint would reject a repeated term anyway; filtering here
    turns a copy-paste slip in the catalog into a warning instead of a failed
    startup that takes the whole seed with it.
    """
    rows: list[SeptaNicheKeyword] = []
    seen: set[str] = set()
    for order, entry in enumerate(entries):
        term = str(entry.get("term") or "").strip()
        if not term:
            continue
        if term.casefold() in seen:
            logger.warning("septa catalog: duplicate keyword %r in niche %r — skipped", term, niche_key)
            continue
        seen.add(term.casefold())
        rows.append(
            SeptaNicheKeyword(
                niche_key=niche_key,
                term=term,
                tier=(entry.get("tier") or None),
                notes=(entry.get("notes") or None),
                sort_order=order,
            )
        )
    return rows


def _code_rows(niche_key: str, entries: list[dict[str, Any]]) -> list[SeptaNicheCode]:
    """Build commodity-code rows, dropping blanks and duplicates."""
    rows: list[SeptaNicheCode] = []
    seen: set[str] = set()
    for order, entry in enumerate(entries):
        code = str(entry.get("code") or "").strip()
        if not code:
            continue
        if code.casefold() in seen:
            logger.warning("septa catalog: duplicate code %r in niche %r — skipped", code, niche_key)
            continue
        seen.add(code.casefold())
        rows.append(
            SeptaNicheCode(
                niche_key=niche_key,
                code=code,
                title=(entry.get("title") or None),
                notes=(entry.get("notes") or None),
                sort_order=order,
            )
        )
    return rows


def seed_niches() -> None:
    """Load `NICHES` into the catalog tables. Called once at startup.

    Best-effort by design: this runs inside `init_db`, and a malformed catalog
    (or a database that is simply down) must not stop the API from serving every
    other portal. Failures are logged and swallowed.
    """
    session = SessionLocal()
    try:
        for key, entry in NICHES.items():
            niche = session.get(SeptaNiche, key)
            if niche is None:
                niche = SeptaNiche(key=key)
                session.add(niche)
            niche.label = str(entry.get("label") or key)
            niche.slug = entry.get("slug") or key
            niche.sort_order = int(entry.get("order") or 0)
            niche.is_active = True

            # Replace this niche's terms wholesale — the file is authoritative,
            # so a term deleted there must disappear here too.
            session.execute(delete(SeptaNicheKeyword).where(SeptaNicheKeyword.niche_key == key))
            session.execute(delete(SeptaNicheCode).where(SeptaNicheCode.niche_key == key))
            session.flush()
            session.add_all(_keyword_rows(key, entry.get("keywords") or []))
            session.add_all(_code_rows(key, entry.get("codes") or []))

        # A niche dropped from the file is retired, not deleted: scraped bids
        # still name it, and deleting would cascade them away.
        retired = session.execute(
            select(SeptaNiche).where(SeptaNiche.key.notin_(list(NICHES)))
        ).scalars().all()
        for niche in retired:
            if niche.is_active:
                logger.info("septa catalog: niche %r no longer in the file — deactivating", niche.key)
            niche.is_active = False

        session.commit()
        logger.info("septa catalog: seeded %d niche(s)", len(NICHES))
    except Exception:  # noqa: BLE001 — seeding must never block API startup
        session.rollback()
        logger.exception("septa catalog: seeding failed — the niche dropdown may be stale or empty")
    finally:
        session.close()


def get_niche(session, key: str) -> SeptaNiche | None:
    """An active niche by key, or None."""
    return session.execute(
        select(SeptaNiche).where(SeptaNiche.key == key, SeptaNiche.is_active.is_(True))
    ).scalar_one_or_none()


def list_niches(session) -> list[SeptaNiche]:
    """Every active niche, in dropdown order."""
    return list(
        session.execute(
            select(SeptaNiche)
            .where(SeptaNiche.is_active.is_(True))
            .order_by(SeptaNiche.sort_order, SeptaNiche.key)
        ).scalars().all()
    )


def niche_terms(key: str) -> tuple[str, list[str], list[str]] | None:
    """This niche's (label, keywords, commodity codes) in catalog order.

    Read through a short-lived session of its own so the scraper — which runs on
    a worker thread with no request-scoped session — can resolve a run's terms
    without holding a connection for the length of the scrape. Returns None if
    the niche is unknown or inactive.
    """
    session = SessionLocal()
    try:
        niche = get_niche(session, key)
        if niche is None:
            return None
        label = niche.label
        keywords = session.execute(
            select(SeptaNicheKeyword.term)
            .where(SeptaNicheKeyword.niche_key == key)
            .order_by(SeptaNicheKeyword.sort_order, SeptaNicheKeyword.id)
        ).scalars().all()
        codes = session.execute(
            select(SeptaNicheCode.code)
            .where(SeptaNicheCode.niche_key == key)
            .order_by(SeptaNicheCode.sort_order, SeptaNicheCode.id)
        ).scalars().all()
        return label, list(keywords), list(codes)
    finally:
        session.close()
