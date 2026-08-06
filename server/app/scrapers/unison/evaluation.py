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


def evaluate(record: dict[str, Any], document_text: str = "") -> dict[str, Any]:
    """Run the shared funnel over one buy and return what to store.

    The returned dict is the storable shape — decision, the standard reason, and
    the working-out kept for auditing. `full_text` is not among it: it is built
    to be read by the evaluator, not by anyone downstream.
    """
    hint, evidence = requirement_hint(record)
    bid_id = record.get("buy_number") or record.get("buyer_number") or "unknown"
    # Trap 1: the title is the Buy Description alone.
    title = (record.get("buy_description") or "").strip()

    result = evaluate_bid(
        bid_id,
        build_full_text(record, document_text),
        naics_code=naics_code(record),
        title=title,
        requirement_hint=hint,
    )

    logger.info(
        "[eval] %s -> %s (%s, rule %s)%s",
        bid_id, result.get("decision"), result.get("requirement_type"),
        result.get("rule"), f" — hinted: {evidence}" if hint else "",
    )
    return {
        "decision": result.get("decision"),
        "reason": result.get("reason"),
        "requirement_type": result.get("requirement_type"),
        "rule": result.get("rule"),
        "location": result.get("location"),
        "requirement_hinted": bool(result.get("hinted")),
        "hint_evidence": evidence,
    }
