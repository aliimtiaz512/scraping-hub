"""The company's evaluation matrix, run over PHLContracts bids.

The matrix is not reimplemented here. `app.scrapers.sam.evaluation.evaluate` is
the one engine — the Rule A/B/C funnel with the live kill-word list — and this
module maps a Philadelphia bid into the shape it expects. Unison reaches it the
same way; three portals now share one set of rules, which is the only way a
PURSUE on one sheet means the same thing as a PURSUE on another.

**What the matrix needs from a bid is its requirement type — is this a supply or
a service — and PHLContracts states that in its line items.**

A Philadelphia bid's description is a single line ("Jackhammers", "6x4 CNG Truck
with 20HD Compactor Body"). Judged on prose alone the funnel cannot tell a
supply from a service, so an ambiguous bid falls through to "unlisted service",
which in US Mainland is a MANUAL_REVIEW. Measured against real bids from a live
run, that put four of five there — including a truck chassis and a box of
jackhammers, which are plainly supplies.

The item blocks are the evidence, and the engine already has a channel for them.
`requirement_hint` is how a portal tells the funnel it can prove the requirement
type **structurally** rather than infer it: a table of things with quantities and
units is not an opinion about what is being bought. Unison passes it from its
Line Item table; PHLContracts passes it from the item blocks the scraper reads.
With the hint, those four resolve to PURSUE/HARDWARE while the
custodial-services bid stays REJECT — the hint can only promote a bid to
HARDWARE, never rescue one from a Rule B exclusion.

Every bid is evaluated and **every bid is kept**. The verdict is a column on the
sheet, not a filter: the city's Open Bids list is the deliverable, and a bid
dropped from it is a bid nobody can check.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.scrapers.sam.evaluation import evaluate as evaluate_bid
from app.scrapers.unison.evaluation import (
    requirement_hint as _line_item_hint,
    resolve_manual_review as _unison_resolve,
)

logger = logging.getLogger(__name__)

#: Header labels whose values say something about *what is being bought*. The
#: rest of the header table is administrative — dates, contacts, addresses — and
#: feeding it to the engine adds words without adding signal.
_INFORMATIVE_HEADER = (
    "description", "type code", "bid type", "commodity", "category",
    "purchase type", "solicitation type",
)


def _items_as_unison_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """PHL item blocks in the shape the line-item hint reads.

    Unison's rule is the one being reused, so the adapter is here rather than a
    second copy of "does this table prove hardware" living in this package.
    """
    rows = []
    for item in items or []:
        rows.append({
            "description": item.get("name") or item.get("specification") or "",
            "unit": item.get("unit") or "",
            "qty": str(item.get("quantity") or ""),
        })
    return rows


def build_full_text(record: dict[str, Any]) -> str:
    """Everything the engine should read about this bid, as one string.

    The title and description first, because the engine weighs a title most
    heavily; then the item lines, which are where a Philadelphia bid actually
    says what it is buying; then the header labels that carry meaning.
    """
    parts: list[str] = [
        str(record.get("description") or ""),
        str(record.get("title") or ""),
    ]

    for item in record.get("items") or []:
        parts.append(" ".join(str(item.get(field) or "") for field in
                              ("name", "quantity", "unit", "specification")))

    header = record.get("extra_header_data") or {}
    for label, value in header.items():
        if any(term in str(label).lower() for term in _INFORMATIVE_HEADER):
            parts.append(f"{label} {value}")

    return "\n".join(part for part in parts if part.strip())


def hint_for(record: dict[str, Any]) -> tuple[str | None, str]:
    """`("HARDWARE", why)` when the item table proves a supply, else `(None, why)`.

    Delegates to Unison's rule — a majority of line items being quantified
    products, with a service-led description overriding — so "what counts as
    structural proof of hardware" is decided once for both portals.
    """
    items = _items_as_unison_rows(record.get("items") or [])
    if not items:
        return None, "no line items to judge from"
    return _line_item_hint({
        "line_items": items,
        "buy_description": record.get("description") or record.get("title") or "",
    })


def evaluate(record: dict[str, Any]) -> dict[str, Any]:
    """Run the matrix over one bid. Returns the storable verdict fields.

    Never raises: an evaluation that fails leaves the bid in the report carrying
    PENDING and the reason it could not be judged, because a bid without a
    verdict is still a bid the city published.
    """
    bid_id = str(record.get("bid_number") or "unknown")
    title = str(record.get("title") or record.get("description") or "").strip()
    hint, evidence = hint_for(record)

    try:
        result = evaluate_bid(
            bid_id,
            build_full_text(record),
            title=title,
            requirement_hint=hint,
        )
    except Exception as exc:  # noqa: BLE001 — a verdict must never sink a bid
        logger.exception("[eval] %s could not be evaluated", bid_id)
        return {
            "decision": "PENDING",
            "reason": f"Evaluation error: {exc.__class__.__name__}",
            "rule": None,
            "requirement_type": None,
            "hint_evidence": evidence,
        }

    verdict = {
        "decision": result.get("decision"),
        "reason": result.get("reason"),
        "rule": result.get("rule"),
        "requirement_type": result.get("requirement_type"),
        "hint_evidence": evidence,
    }
    return resolve(record, verdict)


# -- resolving the borderline -------------------------------------------------
#
# A MANUAL_REVIEW is a bid the matrix would not decide, and every one of them is
# a bid somebody has to read. A sheet that is mostly MANUAL_REVIEW has not
# evaluated anything — it has reprinted the portal with an extra column and
# handed the work back. So the funnel's undecided verdicts are settled here,
# the same way Unison settles its own (`unison.evaluation.resolve_manual_review`,
# reused rather than restated):
#
#   * line items that are quantified goods  -> PURSUE, Rule A. It is a supply.
#   * anything else                         -> REJECT. It is a service the
#                                              allowed list (Rule C) does not
#                                              name, which is out of scope.
#
# **One case is still left for a person**, because settling it would be a guess
# rather than a rule: a bid with no line items *and* a description too thin to
# read. There is no evidence in either direction, and inventing a verdict from
# nothing is worse than the review it saves.
#
# Everything moved keeps the verdict it would have had, in its reason, so a run
# that decided 40 bids without a person can still say which 40 and why.

#: Words that fill a description without naming anything bought. A title made
#: only of these says nothing a rule can act on.
#:
#: Length is deliberately *not* the test. "License Tag Stickers" is twenty
#: characters and perfectly clear; "Annual requirement" is eighteen and means
#: nothing. What separates them is whether any word names a thing, so that is
#: what is counted.
_FILLER_WORDS = frozenset({
    "annual", "yearly", "requirement", "requirements", "see", "attached",
    "attachment", "attachments", "per", "spec", "specs", "specification",
    "specifications", "tbd", "misc", "miscellaneous", "various", "assorted",
    "n/a", "na", "none", "bid", "bids", "contract", "city", "philadelphia",
    "department", "citywide", "for", "and", "of", "the", "to", "with", "a", "an",
    "new", "open", "market", "purchase", "order", "items", "item", "supply",
    "supplies",
})

#: How many words naming something have to survive before a bid is judgeable.
#: Two, not one: a single surviving word is as often a stray as a subject.
_MIN_MEANINGFUL_WORDS = 2


def _meaningful_words(text: str) -> list[str]:
    return [
        word for word in re.findall(r"[a-z0-9][a-z0-9\-/]*", str(text or "").lower())
        if word not in _FILLER_WORDS and len(word) > 1
    ]


def judgeable(record: dict[str, Any]) -> tuple[bool, str]:
    """Is there enough here to settle without a person? With the reason."""
    if record.get("items"):
        return True, f"{len(record['items'])} line item(s) to judge from"
    words = _meaningful_words(record.get("description") or record.get("title") or "")
    if len(words) >= _MIN_MEANINGFUL_WORDS:
        return True, f"a description naming {', '.join(words[:4])}"
    return False, (
        "no line items, and a description that names nothing to judge on"
    )


def resolve(record: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    """Settle a MANUAL_REVIEW verdict, or leave the few that cannot be settled."""
    if verdict.get("decision") != "MANUAL_REVIEW":
        return verdict

    enough, why = judgeable(record)
    if not enough:
        return {
            **verdict,
            "reason": f"{verdict.get('reason') or 'Manual Review required'} — {why}.",
        }

    settled = _unison_resolve(
        {
            "line_items": _items_as_unison_rows(record.get("items") or []),
            "buy_description": record.get("description") or record.get("title") or "",
        },
        verdict,
    )
    if settled.get("decision") != verdict.get("decision"):
        settled["reason"] = (
            f"{settled.get('reason')} (settled by the matrix rather than sent "
            f"to review — {why})"
        )
    return settled


def log_verdict(record: dict[str, Any], verdict: dict[str, Any]) -> None:
    """The matrix result for one bid, in the run's console stream."""
    logger.info("[PHL EVALUATION MATRIX]: Processing Bid #%s",
                record.get("bid_number") or "unknown")
    logger.info(" ├── Title: %r",
                " ".join(str(record.get("description") or "").split())[:100])
    logger.info(" ├── Organization: %s", record.get("organization") or "—")
    logger.info(
        " ├── [MATRIX EVALUATION]: Status -> %s (%s%s)",
        verdict.get("decision") or "PENDING",
        verdict.get("requirement_type") or "unclassified",
        f", rule {verdict['rule']}" if verdict.get("rule") else "",
    )


__all__ = ["build_full_text", "evaluate", "hint_for", "judgeable",
           "log_verdict", "resolve"]
