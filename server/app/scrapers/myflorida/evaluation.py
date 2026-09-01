"""MFMP bid evaluation — the deterministic tiers.

Source of truth: `MFMP_Bid_Evaluation_Criteria.docx` in the repository root
("MyFloridaMarketPlace (MFMP) Bid Evaluation Criteria", Rizviz International
Impex). Architecturally this mirrors the SAM.gov engine — deterministic rules
first, an Ollama resolution layer behind them for what the rules cannot decide
(`myflorida/ollama_bridge.py`) — but every rule here is MFMP's own, written
against UNSPSC commodity codes and Florida agency advertisement types rather
than against NAICS codes and places of performance.

Where the rules came from (§2 of the criteria): a raw sweep of 76 bids compared
against the same 76 after the client marked 31 of them red. About 27 of those 31
fell into categories with no overlap at all with Rizviz's lanes — those are the
deterministic tiers. The rest were construction and facility-trades bids where
the client kept and rejected visually similar postings side by side, which means
the deciding fact is inside the attached documents, not in the title or the code.
That is Tier 3, and it is why this engine has an LLM layer at all.

Decision flow — the order is the design, not an implementation detail:

  STEP 1  Agency Decision / sole source (§5.2)  → MANUAL_REVIEW
  STEP 2  Construction, civil, trades (§5.1)    → MANUAL_REVIEW
  STEP 3  Mixed excluded + included codes (§5.3)→ MANUAL_REVIEW
  STEP 4  A lane hit, no exclusion (Tier 2)     → PURSUE
  STEP 5  Wholly excluded, no lane (Tier 1)     → REJECT
  STEP 6  Nothing matched                       → MANUAL_REVIEW

**Step 1 runs before the lane check on purpose**, and §7 of the criteria says
why: a single-source award notice for a software vendor carries Software/Web
commodity codes, so checking lanes first would auto-PURSUE a notice that is not
open for competition at all. Ordering is what makes §5.2 do its job.

**Tier 3 beats Tier 1 and Tier 2** for the same reason the criteria says
"regardless of commodity code": the whole finding behind Tier 3 is that codes do
not decide these bids.

**Step 6 fails to MANUAL_REVIEW, never to REJECT.** A bid nobody wrote a rule
for is not a bid the client said no to, and silently rejecting it would hide
exactly the postings worth adding a rule for.

Nothing here reads a document. Tier 3 only *routes* a bid to the layer that
does — see `ollama_bridge.resolve`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

PURSUE = "PURSUE"
REJECT = "REJECT"
MANUAL_REVIEW = "MANUAL_REVIEW"

#: An 8-digit UNSPSC code as it appears in the Commodity Codes cell, which the
#: detail parser writes as `42211705 — Hearing aid | 45111701 — Assistive …`.
_CODE_RE = re.compile(r"\b(\d{8})\b")


# ---------------------------------------------------------------------------
# Tier 1 — auto-REJECT categories (criteria §3)
# ---------------------------------------------------------------------------
# `codes` are UNSPSC prefixes, written here as the criteria writes them: an
# 8-digit entry is one code, anything shorter is a family ("85xxxxxx" -> "85").
# `terms` are matched against the title, advertisement type and description.
#
# `goods_safe` marks a category whose exclusion is about a **service**, so an
# equipment or materials purchase on the same subject must not be swept into it
# — §7 of the criteria calls this out specifically: laundry *services* are
# excluded, laundry *equipment* is not. See `_looks_like_goods`.
TIER1_CATEGORIES: tuple[dict[str, Any], ...] = (
    {
        "key": "agriculture",
        "name": "Agriculture, forestry, land & wildlife management",
        "codes": ("70111", "70141", "70151"),
        "terms": (
            "herbicide", "pesticide", "lawn care", "lawn maintenance", "mowing",
            "forestry", "timber harvesting", "seed harvesting", "invasive species",
            "aerial application", "wildlife management", "land management",
        ),
        "goods_safe": False,
    },
    {
        "key": "health_grants",
        "name": "Health & social-services grant programs",
        "codes": ("85", "93131", "93141"),
        "terms": (
            "substance abuse", "foster care", "child welfare", "disbursement unit",
            "behavioral health", "social services",
        ),
        # "grant program" excludes only when it does not fund one of our lanes —
        # the criteria's own qualifier. Handled by the lane check in `evaluate`,
        # which is what "unless it funds a print/design/software/marketing/AI
        # deliverable" means in code.
        "soft_terms": ("grant program", "grant opportunity"),
        "goods_safe": False,
    },
    {
        "key": "waste_relocation",
        "name": "Waste, relocation & roadside services",
        "codes": ("76121501", "78101804", "78141505"),
        "terms": (
            "trash collection", "refuse collection", "solid waste", "garbage",
            "moving and relocation", "relocation services", "towing", "wrecker",
            "road ranger", "risc",
        ),
        "goods_safe": False,
    },
    {
        "key": "real_estate",
        "name": "Real estate & sponsorship",
        "codes": ("80131500", "80141609"),
        "terms": (
            "lease of property", "rental of property", "real property",
            "event sponsorship", "sponsorship opportunity",
        ),
        "goods_safe": False,
    },
    {
        "key": "textile_care",
        "name": "Textile care services",
        "codes": ("91111502", "91111503"),
        "terms": ("laundry service", "dry cleaning", "dry-cleaning", "linen service"),
        # The §7 edge case: a laundry *equipment* purchase is a goods buy and
        # must not be rejected as a textile-care service.
        "goods_safe": True,
    },
    {
        "key": "program_consulting",
        "name": "Generic non-technical program consulting",
        "codes": ("801015", "80111509", "86132"),
        "terms": (
            "program administration", "program evaluation", "programme evaluation",
            "organizational assessment", "strategic planning services",
        ),
        "goods_safe": False,
    },
)

# ---------------------------------------------------------------------------
# Tier 2 — auto-PURSUE lanes (criteria §4)
# ---------------------------------------------------------------------------
TIER2_LANES: tuple[dict[str, Any], ...] = (
    {
        "key": "software_web",
        "name": "Software/Web",
        "codes": ("81111", "81112", "8116", "43232400"),
        "terms": (
            "portal", "application development", "software development",
            "web development", "website", "system maintenance and support",
            "saas", "cloud hosting",
        ),
    },
    {
        "key": "printing",
        "name": "Printing",
        "codes": ("73151900", "82121505", "82121506", "82121511"),
        "terms": (
            "printing services", "industrial printing", "promotional printing",
            "publication printing", "technical manual printing", "print production",
        ),
    },
    {
        "key": "graphic_design",
        "name": "Graphic Design",
        "codes": ("82141", "82151"),
        "terms": ("graphic design", "branding", "creative services", "logo design"),
    },
    {
        "key": "digital_marketing",
        "name": "Digital Marketing",
        "codes": (),
        "terms": (
            "digital marketing", "advertising", "social media", "seo",
            "search engine optimization", "sem", "media buying",
        ),
    },
    {
        "key": "ai_data",
        "name": "AI/Data",
        "codes": (),
        "terms": (
            "artificial intelligence", "machine learning", "data analytics",
            "business intelligence", "automation", "predictive model",
        ),
    },
    {
        "key": "pcb_electronics",
        "name": "PCB/Electronics",
        "codes": ("32", "46"),
        "terms": (
            "printed circuit", "pcb", "electronic components", "fabrication",
            "assembly", "circuit board",
        ),
    },
    {
        "key": "it_staffing",
        "name": "IT staffing/consulting",
        "codes": ("80101507",),
        "terms": (
            "staff augmentation", "information technology consulting",
            "prequalification of information technology", "it consulting",
        ),
    },
)

# ---------------------------------------------------------------------------
# Tier 3 — routed to MANUAL_REVIEW (criteria §5)
# ---------------------------------------------------------------------------
#: §5.1 — building/facility construction, civil infrastructure and trades work.
#: The client made opposite calls on visually similar bids here, so no keyword
#: rule decides them; the scope-of-work document does.
CONSTRUCTION_TERMS: tuple[str, ...] = (
    "roofing", "roof replacement", "paving", "resurfacing", "asphalt",
    "sewer", "water main", "well drilling", "water well", "septic",
    "plumbing", "hvac", "air conditioning", "chiller", "boiler",
    "electrical contractor", "electrical services", "generator installation",
    "marine construction", "dock", "seawall", "dredging",
    "elevator", "escalator", "fire alarm", "fire sprinkler",
    "door replacement", "overhead door", "window replacement",
    "construction", "renovation", "remodel", "demolition",
    "general contractor", "design-build", "site work",
)

#: §5.2 — sole-source / single-source notices. These name a vendor and are not
#: open for competition; the advertisement type alone is strong evidence.
SOLE_SOURCE_AD_TYPES: tuple[str, ...] = ("agency decision",)
SOLE_SOURCE_TERMS: tuple[str, ...] = (
    "single source", "sole source", "intent to award", "notice of intent",
    "non-competitive",
)

#: The client. A notice naming Rizviz is the one sole-source posting that is not
#: automatically uninteresting, which is the whole reason §5.2 is a review and
#: not a rejection.
OWN_COMPANY_TERMS: tuple[str, ...] = ("rizviz", "rizviz international")

#: Goods signals, for the §7 services-vs-goods distinction.
GOODS_TERMS: tuple[str, ...] = (
    "equipment", "machine", "machinery", "purchase of", "supply of", "supplies",
    "materials", "furnish and deliver", "commodities", "parts", "units",
)


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------


def commodity_codes(record: dict[str, Any]) -> list[str]:
    """The 8-digit UNSPSC codes on a record, in the order they appear.

    Read out of the `commodity_codes` cell the detail parser builds
    (`42211705 — Hearing aid | 45111701 — …`) rather than from a separate field,
    so the engine and the spreadsheet can never disagree about what a bid was
    coded as.
    """
    return _CODE_RE.findall(str(record.get("commodity_codes") or ""))


def _haystack(record: dict[str, Any]) -> str:
    """The text the keyword rules read: title, advertisement type, description.

    The description is included but truncated. A Florida advertisement body runs
    to statutory boilerplate — §287.057 notices, public-records clauses — and
    letting all of it match would fire "construction" on the standard clause
    that mentions it. The opening is where the scope is stated.
    """
    parts = [
        str(record.get("title") or ""),
        str(record.get("ad_type") or ""),
        str(record.get("commodity_codes") or ""),
        str(record.get("description") or "")[:1500],
    ]
    return " ".join(parts).lower()


def _title_hay(record: dict[str, Any]) -> str:
    """Title and advertisement type only — the fields that state the subject."""
    return f"{record.get('title') or ''} {record.get('ad_type') or ''}".lower()


#: Every code prefix any tier claims. Used to settle a code claimed by two of
#: them — see `_owns_code`.
_ALL_PREFIXES: tuple[str, ...] = tuple(
    prefix
    for group in (TIER1_CATEGORIES, TIER2_LANES)
    for entry in group
    for prefix in entry["codes"]
)


def _owns_code(code: str, prefixes: tuple[str, ...]) -> bool:
    """Does one of `prefixes` own this code — i.e. is it the most specific claim?

    The criteria puts `80101500 family` in Tier 1 (generic program consulting)
    and `80101507` in Tier 2 (IT consultation), so a bid coded 80101507 matches
    both and reads as a §5.3 "mixed codes" review — when it is nothing of the
    sort. It is one code, named exactly once, by the lane.

    Longest prefix wins, which is how a code hierarchy is meant to be read: the
    more specific rule is the one that was written about this code, and the
    shorter one is the family it happens to sit in.
    """
    best = max((len(p) for p in _ALL_PREFIXES if code.startswith(p)), default=0)
    return any(code.startswith(p) and len(p) == best for p in prefixes)


def _code_matches(codes: list[str], prefixes: tuple[str, ...]) -> list[str]:
    """Every code these prefixes own."""
    return [code for code in codes if _owns_code(code, prefixes)]


#: Cache of compiled term matchers, keyed by the term itself.
_TERM_RES: dict[str, re.Pattern[str]] = {}


def _term_re(term: str) -> re.Pattern[str]:
    """A whole-word matcher for one term.

    Substring matching is not safe at this size of vocabulary: "sem" is inside
    "disbursement", "seo" inside "seoul", "risc" inside "riscograph". A bid for
    foster-care disbursement units was reading as a Digital Marketing lane hit
    on the strength of three letters in the middle of a word, which then made it
    a §5.3 "mixed codes" review instead of the Tier 1 rejection it is.
    """
    pattern = _TERM_RES.get(term)
    if pattern is None:
        pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")
        _TERM_RES[term] = pattern
    return pattern


def _terms_present(hay: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if _term_re(term).search(hay)]


def _looks_like_goods(hay: str) -> bool:
    """Is this a purchase of equipment or materials rather than a service?

    §7 of the criteria: "Equipment/materials purchases … read very differently
    from services contracts on the same subject — keep the services-vs-goods
    distinction in the Tier 1 checks so equipment purchases aren't wrongly swept
    into a services exclusion."
    """
    return bool(_terms_present(hay, GOODS_TERMS))


def _lane_hits(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Every Tier 2 lane this bid touches, with what matched."""
    codes = commodity_codes(record)
    hay = _haystack(record)
    hits = []
    for lane in TIER2_LANES:
        matched_codes = _code_matches(codes, lane["codes"]) if lane["codes"] else []
        matched_terms = _terms_present(hay, lane["terms"])
        if matched_codes or matched_terms:
            hits.append({
                "key": lane["key"], "name": lane["name"],
                "codes": matched_codes, "terms": matched_terms,
            })
    return hits


def _exclusion_hits(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Every Tier 1 category this bid falls into, with what matched.

    A category whose exclusion is about a service does not fire on a goods
    purchase — the §7 rule, applied per category rather than globally so the
    word "equipment" cannot rescue a bid from a category that never had a
    goods/services distinction to make.
    """
    codes = commodity_codes(record)
    hay = _haystack(record)
    goods = _looks_like_goods(hay)
    hits = []
    for category in TIER1_CATEGORIES:
        if category["goods_safe"] and goods:
            continue
        matched_codes = _code_matches(codes, category["codes"])
        matched_terms = _terms_present(hay, category["terms"])
        matched_soft = _terms_present(hay, category.get("soft_terms", ()))
        if matched_codes or matched_terms or matched_soft:
            hits.append({
                "key": category["key"], "name": category["name"],
                "codes": matched_codes,
                "terms": matched_terms + matched_soft,
                # A soft-only match is the "grant program unless it funds one of
                # our deliverables" case: real, but not on its own a rejection.
                "soft_only": not (matched_codes or matched_terms),
            })
    return hits


def _is_sole_source(record: dict[str, Any]) -> tuple[bool, list[str]]:
    """§5.2 — a notice naming a vendor rather than inviting competition."""
    ad_type = str(record.get("ad_type") or "").lower()
    hay = _haystack(record)
    signals = [t for t in SOLE_SOURCE_AD_TYPES if t in ad_type]
    signals += _terms_present(hay, SOLE_SOURCE_TERMS)
    return bool(signals), signals


def _names_own_company(record: dict[str, Any]) -> bool:
    return bool(_terms_present(_haystack(record), OWN_COMPANY_TERMS))


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def evaluate(record: dict[str, Any]) -> dict[str, Any]:
    """Classify one advertisement. Never raises, never reads a document.

    Returns `decision`, `reason`, `rule` and `evidence`, plus `needs_documents`
    — True when the decision is a Tier 3 routing and the LLM layer should read
    the bid's attachments before anything is written down.

    The record is not modified; the caller merges the result.
    """
    try:
        return _evaluate(record)
    except Exception as exc:  # noqa: BLE001 — a bid is never worth a failed run
        logger.exception("MFMP evaluation failed for %s", record.get("ad_number"))
        return {
            "decision": MANUAL_REVIEW,
            "reason": f"Evaluation error ({exc.__class__.__name__}) — needs a person",
            "rule": "error",
            "evidence": "",
            "needs_documents": False,
        }


def _evaluate(record: dict[str, Any]) -> dict[str, Any]:
    lanes = _lane_hits(record)
    exclusions = _exclusion_hits(record)
    hard_exclusions = [hit for hit in exclusions if not hit["soft_only"]]

    # -- STEP 1: §5.2 sole source ------------------------------------------
    # Ahead of the lane check because §7 says so: a single-source award notice
    # for a software vendor carries Software/Web codes, and checking lanes first
    # would pursue a contract that is not open for competition.
    sole_source, signals = _is_sole_source(record)
    if sole_source and not _names_own_company(record):
        return {
            "decision": MANUAL_REVIEW,
            "reason": (
                "Sole-source / Agency Decision notice naming another vendor — "
                "not open for competition (§5.2)"
            ),
            "rule": "5.2 sole source",
            "evidence": ", ".join(signals),
            "needs_documents": True,
        }

    # -- STEP 2: §5.1 construction, civil infrastructure, trades -----------
    # Regardless of commodity code, per §5: the client kept and rejected
    # near-identical bids here, so only the scope-of-work document decides.
    construction = _terms_present(_haystack(record), CONSTRUCTION_TERMS)
    if construction:
        return {
            "decision": MANUAL_REVIEW,
            "reason": (
                "Construction / facility-trades scope — contract value, licensing "
                "and on-site labour decide this one (§5.1)"
            ),
            "rule": "5.1 construction/trades",
            "evidence": ", ".join(construction),
            "needs_documents": True,
        }

    # -- STEP 3: §5.3 mixed codes ------------------------------------------
    if lanes and hard_exclusions:
        return {
            "decision": MANUAL_REVIEW,
            "reason": (
                f"Spans an excluded category ({hard_exclusions[0]['name']}) and a "
                f"service lane ({lanes[0]['name']}) (§5.3)"
            ),
            "rule": "5.3 mixed codes",
            "evidence": _describe(lanes + hard_exclusions),
            "needs_documents": True,
        }

    # -- STEP 4: Tier 2 lane ------------------------------------------------
    # A soft exclusion does not block this: "grant program" excludes *unless* it
    # funds a print/design/software/marketing/AI deliverable, and a lane hit is
    # exactly that funding.
    if lanes:
        return {
            "decision": PURSUE,
            "reason": f"In scope — {lanes[0]['name']} lane (Tier 2)",
            "rule": f"Tier 2 — {lanes[0]['name']}",
            "evidence": _describe(lanes),
            "needs_documents": False,
        }

    # -- STEP 5: Tier 1 exclusion ------------------------------------------
    if hard_exclusions:
        return {
            "decision": REJECT,
            "reason": f"Out of scope — {hard_exclusions[0]['name']} (Tier 1)",
            "rule": f"Tier 1 — {hard_exclusions[0]['name']}",
            "evidence": _describe(hard_exclusions),
            "needs_documents": False,
        }

    # -- STEP 6: nothing matched -------------------------------------------
    # Not a rejection. A bid no rule covers is a bid nobody has ruled on, and
    # rejecting it silently would bury the postings worth writing a rule for.
    return {
        "decision": MANUAL_REVIEW,
        "reason": "No Tier 1 exclusion and no Tier 2 lane matched — needs a person",
        "rule": "unmatched",
        "evidence": "",
        "needs_documents": True,
    }


def _describe(hits: list[dict[str, Any]]) -> str:
    """What matched, short enough for a spreadsheet cell."""
    parts = []
    for hit in hits:
        signals = (hit.get("codes") or []) + (hit.get("terms") or [])
        parts.append(f"{hit['name']}: {', '.join(signals[:4])}" if signals else hit["name"])
    return " | ".join(parts)


def log_verdict(record: dict[str, Any], verdict: dict[str, Any]) -> None:
    """One line per bid, so a run's decisions are readable in the log."""
    logger.info(
        " ├── [EVALUATION]: %s -> %s (%s)",
        record.get("ad_number") or "?", verdict.get("decision"), verdict.get("rule"),
    )
