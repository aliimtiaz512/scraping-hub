"""Curated BidNet Direct keyword catalog, organized by niche and tier.

The catalog is EMPTY pending a new keyword set. The previous catalog (AI/ML,
Web Scraping, UI/UX — sourced from BidNet_Direct_Keyword_Reference.docx) was
removed on 2026-07-31; recover it from git history if needed.

Shape to restore it with — each niche has a **core** tier (high-yield, run
first) and an **extended** tier (specialized):

    NICHES = {
        "niche_key": {
            "label": "Niche Label",     # shown in run status / Excel `niche`
            "slug": "Niche-Label",      # used in folder names
            "core":     [{"term": "...", "notes": "..."}, ...],
            "extended": [{"term": "...", "notes": "..."}, ...],
        },
    }

The scraper searches each keyword *separately* (never concatenated) and folders
results per niche+tier (see `group_keywords`). Terms must be unique across the
whole catalog — a term maps to exactly one niche+tier.
"""

# niche key -> {label, slug (used in folder names), core[], extended[]}
# Each keyword entry is {term, notes}. Tier is implied by the list it lives in.
# EMPTY — awaiting the new keyword set (see module docstring for the shape).
NICHES: dict[str, dict] = {}

# Folder name for keywords typed by hand (no niche/tier).
CUSTOM_FOLDER = "Bidnetdirect_Custom"

# The portal's own cap on its search box:
#   <textarea id="solicitationSingleBoxSearch" name="keywords" maxlength="1000">
# Anything longer is silently truncated by the browser, which would quietly
# search for something other than what was asked — so it is rejected up front.
MAX_KEYWORD_LENGTH = 1000

# Guard against a paste that would turn one click into a multi-hour run: each
# keyword is its own full search + pagination pass over the portal.
MAX_KEYWORDS = 100


def clean_keywords(keywords: list[str]) -> list[str]:
    """Strip, drop blanks, and de-duplicate while preserving the caller's order."""
    return list(dict.fromkeys(kw.strip() for kw in keywords if kw.strip()))


def validate_keywords(keywords: list[str]) -> list[str]:
    """Check typed keywords against the portal's search box. Returns readable
    problems; an empty list means they are usable.

    A keyword may be a whole boolean expression — the search box documents
    `AND`, `OR` and parenthesised grouping — so nothing here parses or splits
    them. They are passed to the portal verbatim, one search each.
    """
    problems: list[str] = []
    if len(keywords) > MAX_KEYWORDS:
        problems.append(
            f"{len(keywords)} keywords is more than the {MAX_KEYWORDS} a single run allows — "
            "each one is a separate search of the whole portal."
        )
    for keyword in keywords:
        if len(keyword) > MAX_KEYWORD_LENGTH:
            problems.append(
                f"keyword is {len(keyword)} characters; BidNet's search box takes at most "
                f"{MAX_KEYWORD_LENGTH}: “{keyword[:60]}…”"
            )
    return problems


# term -> {niche_key, label, slug, tier}. Built once; terms are unique across the
# whole catalog, so a term maps to exactly one niche+tier.
_TERM_INDEX: dict[str, dict] = {}
for _key, _niche in NICHES.items():
    for _tier in ("core", "extended"):
        for _kw in _niche[_tier]:
            _TERM_INDEX[_kw["term"]] = {
                "niche_key": _key,
                "label": _niche["label"],
                "slug": _niche["slug"],
                "tier": _tier,
            }

# Flat set of every catalog term (the scrape endpoint still accepts arbitrary
# custom terms not in this set).
VALID_TERMS: set[str] = set(_TERM_INDEX)


def catalog_terms() -> list[str]:
    """Every catalog term, in catalog order (niche, then core before extended).

    This is what a run searches: the client no longer selects keywords, so the
    server drives the whole catalog. Empty until a new catalog is configured.
    """
    return [
        kw["term"]
        for niche in NICHES.values()
        for tier in ("core", "extended")
        for kw in niche[tier]
    ]


def _folder_name(slug: str, tier: str) -> str:
    return f"Bidnetdirect_{slug}_{tier}"


def group_keywords(keywords: list[str]) -> list[dict]:
    """Split a flat selected-keyword list into ordered niche+tier groups.

    Each returned group is a self-contained scrape+storage unit:
        {niche_key, label, slug, tier, folder_name, keywords: [...]}
    Catalog terms map to their niche+tier; anything not in the catalog is
    collected into a single 'custom' group (Bidnetdirect_Custom). Groups follow
    catalog order (niche, then core before extended), custom last; keyword order
    within a group follows the catalog (custom follows input order). Blank and
    duplicate terms are dropped.
    """
    cleaned = list(dict.fromkeys(kw.strip() for kw in keywords if kw.strip()))
    chosen = set(cleaned)

    groups: list[dict] = []
    # Catalog groups in a stable order.
    for niche_key, niche in NICHES.items():
        for tier in ("core", "extended"):
            terms = [kw["term"] for kw in niche[tier] if kw["term"] in chosen]
            if terms:
                groups.append({
                    "niche_key": niche_key,
                    "label": f"{niche['label']} · {tier.capitalize()}",
                    "slug": niche["slug"],
                    "tier": tier,
                    "folder_name": _folder_name(niche["slug"], tier),
                    "keywords": terms,
                })

    # Custom terms (not in the catalog), preserving input order.
    custom = [term for term in cleaned if term not in VALID_TERMS]
    if custom:
        groups.append({
            "niche_key": "custom",
            "label": "Custom",
            "slug": "Custom",
            "tier": "custom",
            "folder_name": CUSTOM_FOLDER,
            "keywords": custom,
        })

    return groups
