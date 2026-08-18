"""Excluded niches: the bids a reader should see are not worth reading.

The client keeps twenty service categories out of scope. A PHLContracts run does
not reject those bids — the city's Open Bids list is the deliverable and a bid
dropped from it is a bid nobody can check — but it does mark them, so a reviewer
opening the sheet can skip past them instead of reading each title to work out
which are worth their time.

**The list is not written out here.** It is `RULE_B` in the SAM evaluator, which
is the same client's same twenty categories, already in this codebase and
already carrying the names they use for them. Copying it would mean a second
place to edit when the client adds a twenty-first, and the two would drift
without anyone noticing until a sheet disagreed with a SAM verdict. This module
imports it and derives the search terms — `evaluator.py` imports only `logging`
and `re`, so there is no weight to the dependency.

Matching is on whole words. "audit" as a substring matches "auditorium" and
"auditory", which would flag a bid for a concert hall as an excluded audit
service; the same trap the GSA screen fell into on the Unison side. Every term
here is matched with word boundaries at each end.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.scrapers.sam.engine.evaluator import RULE_B

logger = logging.getLogger(__name__)


def _variants(name: str) -> set[str]:
    """The forms a category name is written in.

    The client writes "Construction & Demolition Services" and a bid says
    "construction and demolition"; both are the same category. Rather than list
    each spelling by hand, the `&`/`and` pair is generated from the one name.
    """
    lowered = " ".join(name.lower().split())
    forms = {lowered}
    if "&" in lowered:
        forms.add(lowered.replace("&", "and"))
    if " and " in lowered:
        forms.add(lowered.replace(" and ", " & "))
    return {" ".join(f.split()) for f in forms}


#: Every written form of every excluded category, longest first — so a bid
#: matching "waste management services" is reported under that rather than under
#: the shorter "management services" it also contains.
EXCLUDED_NICHES: tuple[str, ...] = tuple(
    sorted(
        {form for name in RULE_B.values() for form in _variants(name)},
        key=lambda term: (-len(term), term),
    )
)

#: One pattern per term, matched on whole words. `\s+` between the words rather
#: than a literal space, so a description broken across a line break still
#: matches — the portal's own text is full of them.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (term, re.compile(r"\b" + r"\s+".join(re.escape(w) for w in term.split()) + r"\b",
                      re.IGNORECASE))
    for term in EXCLUDED_NICHES
)

#: The fields a niche can be named in. The portal has no Category on a bid, so
#: the description is doing most of the work; `title` and `category` are read
#: for the records that carry them and for whatever a later field adds.
INSPECTED_FIELDS: tuple[str, ...] = ("description", "title", "category", "buyer_description")


def combined_text(record: dict[str, Any]) -> str:
    """The inspected fields as one normalised string.

    Whitespace collapsed rather than only stripped: a description with a line
    break inside "custodial\\nservices" is the same phrase as one without.
    """
    parts = [str(record.get(field) or "") for field in INSPECTED_FIELDS]
    return " ".join(" ".join(parts).split())


def check(record: dict[str, Any]) -> tuple[bool, str]:
    """`(flagged, matched niche)` for one bid. `("", False)` when it is clean.

    The matched term is returned, not just the fact of a match, because a row
    marked FLAGGED with no reason is one a reviewer has to re-derive by reading
    the description — which is the work this is meant to save.
    """
    text = combined_text(record)
    if not text:
        return False, ""
    for term, pattern in _PATTERNS:
        if pattern.search(text):
            return True, term
    return False, ""


def status(record: dict[str, Any]) -> str:
    """The Status cell: FLAGGED, or empty for a bid in scope."""
    return "FLAGGED" if check(record)[0] else ""


def reason(record: dict[str, Any]) -> str:
    """The Flag Reason cell: the niche that matched, in the client's own words."""
    matched = check(record)[1]
    return f"Excluded niche — {matched}" if matched else ""


def log_match(record: dict[str, Any], matched: str) -> None:
    """Announce an interception, with the text it was made on."""
    description = " ".join(str(record.get("description") or "").split())
    logger.info("[EVALUATION]: Processing Bid #%s",
                record.get("bid_number") or "unknown")
    logger.info(" ├── Description: %r", description[:120])
    logger.info(" ├── [RED FLAG TRIGGERED]: Matched Niche -> %r", matched)
    logger.info(" └── [ACTION]: Tagged as FLAGGED (Row highlighted RED in Excel export)")
