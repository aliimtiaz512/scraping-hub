"""Turning a scraped buy into the four inputs the evaluator takes, and back.

The decision itself is not made here. It is made by the shared funnel in
`app.scrapers.sam.evaluation`, against Company_Bid_Selection_Criteria.docx —
the same rules, the same order, the same standard reason phrases SAM produces.
What this module owns is the mapping: which part of a Unison detail page becomes
`title`, which becomes `full_text`, and when the requirement type can be stated
outright rather than inferred.

Two pieces of the page are deliberately withheld from the classifier, both
because testing showed they produce false rejections:

  * **Category / Subcategory.** `7B20 -- … (HARDWARE AND PERPETUAL LICENSE
    SOFTWARE)` contains the word SOFTWARE, which matches Rule B #3 "Management
    Software" and rejects a hardware buy.
  * **Buy Terms and Bidding Requirements.** The FAR boilerplate mentions
    maintenance, training, audit and research and development in passing; fed to
    the classifier it rejects under Rule B #20 (R&D).

Both still reach the evaluator — as body text, where they inform location
detection and nothing else. Only the Buy Description classifies. This mirrors
the SAM engine's own rule that the notice title classifies and the document
body does not.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.scrapers.sam.evaluation import evaluate as evaluate_bid

logger = logging.getLogger(__name__)

# Units that describe a countable physical thing. A line item measured in these
# is being supplied, not performed.
_PRODUCT_UNITS = {
    "each", "ea", "unit", "units", "lot", "lots", "box", "boxes", "case", "cases",
    "pkg", "package", "packages", "set", "sets", "kit", "kits", "pair", "pairs",
    "roll", "rolls", "dozen", "carton", "cartons", "bx", "cs", "pc", "pcs", "piece",
}

# A description carrying one of these is work performed, whatever its unit —
# "Grease Trap Cleaning ... 1 LOT" is a service with a product-shaped row.
_SERVICE_TERMS = (
    "installation", "install", "maintenance", "repair", "servicing", "cleaning",
    "janitorial", "custodial", "training", "inspection", "audit", "removal",
    "disposal", "demolition", "construction", "consulting", "staffing", "survey",
    "study", "assessment", "rental", "lease", "management services", "support services",
    "labor", "technician", "engineering services",
)

# Enough of the line items must look like products for the buy to be one.
_PRODUCT_SHARE = 0.6

_DIGITS = re.compile(r"\d")


# ---------------------------------------------------------------------------
# Early-exit screens — decided before the funnel, on what the portal states
# ---------------------------------------------------------------------------
#
# These are not rules the classifier could reach. They read fields the classifier
# is deliberately not given (Category, and the contract-vehicle line in General
# Information), and they act on what the portal *declares* rather than on what
# the prose implies. A buy that says "GSA Schedules" in its contract vehicle is
# not a borderline case to be weighed against Rules B and C — it is off the table
# before the weighing starts, and running it through the funnel only risks the
# funnel disagreeing.
#
# Each screen is (rule code, human reason, matcher). The rule code lands in the
# `rule` column so a rejection is traceable to the screen that made it, and is
# kept short — the column is String(16).

#: Contract vehicles that put a buy out of scope, matched case-insensitively in
#: the Buy Description and in the General Information contract-vehicle line.
GSA_TERMS = ("gsa schedules", "gsa schedule", "gsa federal supply schedule")

#: Categories out of scope outright. Matched against Category and Subcategory,
#: normalised (lowercased, punctuation and the portal's numeric prefix dropped),
#: so `31A5 -- Hospitality and Food Services` matches on its words alone.
EXCLUDED_CATEGORY_TERMS = (
    "hospitality and food services",
    "hospitality",
    "food service",
    "food services",
    "foods",
    "catering",
    "beverage",
    "restaurant",
)

_CATEGORY_PREFIX = re.compile(r"^\s*[0-9a-z]{1,6}\s*--\s*", re.IGNORECASE)


def _normalise(text: str) -> str:
    """Lowercase, portal code prefix dropped, whitespace collapsed."""
    cleaned = _CATEGORY_PREFIX.sub("", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def _contract_vehicle_text(record: dict[str, Any]) -> str:
    """Everything that can name a contract vehicle, as one searchable string.

    The portal has no single Contract Vehicle field: it appears in the Buy
    Description on some buys and as a General Information row on others (its
    label varies, so unmapped rows land in `general_info["extra"]`). All of it
    is searched — a screen that reads one of the two places would pass exactly
    the buys that state it in the other.
    """
    general = record.get("general_info") or {}
    extra = general.get("extra") or {}
    parts = [
        record.get("buy_description") or "",
        general.get("buy_description") or "",
        general.get("solicitation_number") or "",
        general.get("sam_contract_opportunity") or "",
        " ".join(f"{k} {v}" for k, v in extra.items() if v),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def screen(record: dict[str, Any]) -> tuple[str, str, str] | None:
    """The early-exit verdict for this buy, or None to send it to the funnel.

    Returns `(rule, decision, reason)`. Only ever REJECT: a screen exists to take
    a buy off the table cheaply, never to put one on it — that judgement stays
    with the funnel, which weighs the whole record.
    """
    vehicle = _contract_vehicle_text(record)
    matched = next((term for term in GSA_TERMS if term in vehicle), None)
    if matched:
        return (
            "screen:gsa",
            "REJECT",
            f"Contract vehicle is {matched.upper()} — outside the company's "
            f"contracting routes, so the buy is not pursued.",
        )

    for field in ("category", "subcategory"):
        value = _normalise(record.get(field) or "")
        if not value:
            continue
        hit = next((term for term in EXCLUDED_CATEGORY_TERMS if term in value), None)
        if hit:
            return (
                "screen:cat",
                "REJECT",
                f"{field.capitalize()} is {record.get(field)} — hospitality and food "
                f"services are outside the company's lines of business.",
            )
    return None


def _is_product_row(item: dict[str, Any]) -> bool:
    description = (item.get("description") or "").lower()
    if not description:
        return False
    if any(term in description for term in _SERVICE_TERMS):
        return False
    unit = (item.get("unit") or "").strip().lower()
    qty = (item.get("qty") or "").strip()
    return unit in _PRODUCT_UNITS and bool(_DIGITS.search(qty))


def requirement_hint(record: dict[str, Any]) -> tuple[str | None, str]:
    """Whether this buy is demonstrably a supply, and the evidence for saying so.

    Returns `("HARDWARE", why)` or `(None, why)`. The hint is only ever
    "HARDWARE": it exists to correct a NAICS band that reads a reseller's supply
    contract as a service, and it can only promote (see `evaluate_bid`).

    The evidence is structural rather than textual — a table of things with
    quantities and units — which is why it is trusted over the NAICS. Shipping
    is ignored as a line: it appears as its own row on most buys and describes
    no goods.
    """
    items = [
        item for item in (record.get("line_items") or [])
        if (item.get("description") or "").strip().lower() not in ("", "shipping")
    ]
    if not items:
        return None, "no line items to judge from"

    # A service-led buy description settles it regardless of how the rows look.
    description = (record.get("buy_description") or "").lower()
    matched = next((term for term in _SERVICE_TERMS if term in description), None)
    if matched:
        return None, f"buy description names a service ({matched})"

    products = [item for item in items if _is_product_row(item)]
    share = len(products) / len(items)
    if share >= _PRODUCT_SHARE:
        return "HARDWARE", f"{len(products)} of {len(items)} line items are quantified products"
    return None, f"only {len(products)} of {len(items)} line items look like products"


def naics_code(record: dict[str, Any]) -> str:
    """The bare NAICS code from `541519 -- EXCEPTION - IT Value Added Resellers`."""
    match = re.match(r"\s*(\d{2,6})", str(record.get("naics") or ""))
    return match.group(1) if match else ""


def build_full_text(record: dict[str, Any], document_text: str = "") -> str:
    """Everything the evaluator may read as *body*: the whole buy, in order.

    Used for location detection and keyword sieves, never for classification —
    the classifier reads `title` alone.
    """
    parts: list[str] = []

    def section(heading: str, body: str) -> None:
        if body and body.strip():
            parts.append(f"{heading}:\n{body.strip()}")

    section("Buy Description", record.get("buy_description") or "")
    general = "\n".join(
        f"{label}: {value}"
        for label, value in (record.get("general_info") or {}).items()
        if value and label != "extra"
    )
    section("General Information", general)

    section("Line Items", "\n".join(
        f"{item.get('no', '')} {item.get('description', '')} "
        f"({item.get('qty', '')} {item.get('unit', '')})".strip()
        for item in (record.get("line_items") or [])
    ))
    section("Bidding Requirements", "\n".join(
        f"{r.get('name', '')}: {r.get('text', '')}"
        for r in (record.get("bidding_requirements") or [])
    ))
    section("Buy Terms", "\n".join(
        f"{t.get('name', '')}: {t.get('text', '')}"
        for t in (record.get("buy_terms") or [])
    ))

    shipping = record.get("shipping") or {}
    # Written as a place, not as three fields: the evaluator's location detector
    # reads prose, and a bare "Buenos Aires" in a column tells it nothing.
    place = ", ".join(v for v in (
        shipping.get("city"), shipping.get("state"), shipping.get("zip"),
    ) if v)
    section("Place of Performance", place)
    section("Buyer", record.get("buyer") or "")
    section("Seller Attachments Required", record.get("seller_attachments_required") or "")
    section("Attached Documents", document_text)

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Manual review: when it is warranted, and when it is just indecision
# ---------------------------------------------------------------------------
#
# The shared funnel returns MANUAL_REVIEW for exactly one case: a **service**
# that matches neither the excluded list (Rule B) nor the allowed list (Rule C),
# located in the US mainland. Every other path decides. That single case was
# swallowing most of a Unison run, because Unison's buys are short
# reseller-style descriptions that rarely name a service from either list.
#
# Two things are done about it here, in this order, and both are Unison-local:
# the funnel is shared with SAM, whose reviewers *want* that queue, and changing
# it would silently change SAM's output.
#
#   1. **Answer it from evidence the funnel never saw.** Unison publishes a Line
#      Item table. A buy whose rows are quantified goods is a supply, whatever a
#      terse description made the classifier think — that is a decision, not a
#      judgement call, so it is made rather than deferred.
#   2. **Fail closed on what remains.** A service on neither list, with no
#      product evidence, is not in a line of business the criteria list. Strict
#      evaluation says REJECT and names why, rather than parking it in a queue
#      nobody empties.
#
# The pre-strict verdict is kept on every record this touches
# (`decision_before_strict`), so the bids the strictness removed can be listed
# and audited — the point is fewer manual reviews, not fewer traceable ones.

#: Resolve the funnel's MANUAL_REVIEW rather than passing it through. Off sends
#: every borderline buy to the queue, which is what the flow did before.
STRICT_FALLBACK = True

#: Enough product-shaped line items to call a buy a supply when the funnel could
#: not classify it. Lower than `_PRODUCT_SHARE`, deliberately: this is not the
#: bar for overriding a NAICS band (that is `requirement_hint`), it is the bar
#: for choosing between an evidenced answer and no answer at all.
_MANUAL_PRODUCT_SHARE = 0.5


def _product_evidence(record: dict[str, Any]) -> tuple[bool, str]:
    """Do this buy's line items show goods being supplied? With the count."""
    items = [
        item for item in (record.get("line_items") or [])
        if (item.get("description") or "").strip().lower() not in ("", "shipping")
    ]
    if not items:
        return False, "no line items"
    products = [item for item in items if _is_product_row(item)]
    share = len(products) / len(items)
    return share >= _MANUAL_PRODUCT_SHARE, f"{len(products)} of {len(items)} line items are quantified products"


def resolve_manual_review(record: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Turn a MANUAL_REVIEW verdict into a decision, or leave it alone.

    Returns the result to store. Anything it changes is recorded on the result
    itself — `decision_before_strict` and the reason it moved — so a run can say
    exactly which buys were decided here instead of by a person.
    """
    if not STRICT_FALLBACK or result.get("decision") != "MANUAL_REVIEW":
        return result

    is_product, evidence = _product_evidence(record)
    if is_product:
        return {
            **result,
            "decision": "PURSUE",
            "requirement_type": "HARDWARE",
            "rule": "A",
            "reason": (
                "Hardware/product requirement — the buy's line items are quantified "
                f"goods ({evidence}), which is in scope under Rule A."
            ),
            "decision_before_strict": "MANUAL_REVIEW",
        }
    return {
        **result,
        "decision": "REJECT",
        "reason": (
            "Service not named on the allowed list (Rule C) and not evidenced as a "
            f"product buy ({evidence}) — outside the company's lines of business."
        ),
        "decision_before_strict": "MANUAL_REVIEW",
    }


def evaluate(record: dict[str, Any], document_text: str = "") -> dict[str, Any]:
    """Decide one buy: the early-exit screens, then the shared funnel.

    The returned dict is the storable shape — decision, the standard reason, and
    the working-out kept for auditing. `full_text` is not among it: it is built
    to be read by the evaluator, not by anyone downstream.
    """
    hint, evidence = requirement_hint(record)
    bid_id = record.get("buy_number") or record.get("buyer_number") or "unknown"

    # The screens run first and skip the funnel entirely. A GSA Schedules buy or
    # a hospitality one is not a close call to be weighed — it is out, and the
    # cheapest correct thing is to say so without reading its documents through
    # a classifier that might disagree.
    screened = screen(record)
    if screened is not None:
        rule, decision, reason = screened
        logger.info("[eval] %s -> %s (early exit, %s)", bid_id, decision, rule)
        return {
            "decision": decision,
            "reason": reason,
            "requirement_type": None,
            "rule": rule,
            "location": None,
            "requirement_hinted": False,
            "hint_evidence": evidence,
        }

    # Trap 1: the title is the Buy Description alone.
    title = (record.get("buy_description") or "").strip()

    result = evaluate_bid(
        bid_id,
        build_full_text(record, document_text),
        naics_code=naics_code(record),
        title=title,
        requirement_hint=hint,
    )

    stored = {
        "decision": result.get("decision"),
        "reason": result.get("reason"),
        "requirement_type": result.get("requirement_type"),
        "rule": result.get("rule"),
        "location": result.get("location"),
        "requirement_hinted": bool(result.get("hinted")),
    }
    stored = resolve_manual_review(record, stored)

    logger.info(
        "[eval] %s -> %s (%s, rule %s)%s%s",
        bid_id, stored.get("decision"), stored.get("requirement_type"),
        stored.get("rule"), f" — hinted: {evidence}" if hint else "",
        f" — was {stored['decision_before_strict']} before the strict fallback"
        if stored.get("decision_before_strict") else "",
    )
    stored["hint_evidence"] = evidence
    return stored
