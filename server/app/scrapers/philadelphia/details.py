"""The detail page's header table and line items, turned into things a reader opens.

Both used to be a problem for the same reason. The header table was written
beside each bid as `Extra_Header_Info.json`, and the line items were not
captured at all — so a procurement officer unpacking the ZIP got a file their
machine offers to open in a code editor, and no item detail whatsoever.

This module is the translation layer:

* `promote_header` pulls the three fields worth sorting a spreadsheet on —
  fiscal year, procurement type, pre-bid conference — out of a label/value table
  whose labels vary per bid type.
* `additional_header` folds everything else the portal published into one
  readable cell, so the sheet carries the whole header table without needing a
  column (and a migration) per label.
* `render_items_text` writes `bid_items_details.txt` — the line items as plain
  text, which is the format that needs no software to read.

The label matching is deliberately loose. BSO prints the same field as "Fiscal
Year" on one bid type and "FY" on another, and a formal solicitation carries
labels a micro purchase does not. Matching on a normalised form against a list
of aliases is what keeps a column populated when the city renames a row.
"""

from __future__ import annotations

import re
from typing import Any

#: A label as it is compared: lowercase, punctuation gone, spaces collapsed. So
#: "Pre-Bid Conference:", "PRE BID CONFERENCE" and "Pre_Bid_Conference" all meet.
_NORMALISE = re.compile(r"[^a-z0-9]+")


def normalise(label: str) -> str:
    return _NORMALISE.sub(" ", (label or "").lower()).strip()


#: Which published labels feed which promoted column, best match first. These are
#: matched on the normalised label as a whole word-ish prefix, not a substring:
#: "bid type" must not be satisfied by "bid type code" landing in the wrong one,
#: so the more specific aliases are listed before the general ones.
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "fiscal_year": ("fiscal year", "fy", "budget year"),
    "solicitation_type": (
        "procurement type", "solicitation type", "purchase method",
        "bid type", "type code", "award type",
    ),
    "pre_bid_conference": (
        "pre bid conference", "prebid conference", "pre bid meeting",
        "pre proposal conference", "pre bid conference date", "conference date",
    ),
}

#: Labels that carry no information for a reader of the sheet: they duplicate a
#: column that is already there, or they are the attachment list, which is what
#: the bid's folder is for.
_UNINTERESTING = {
    "bid number", "description", "bid opening date", "organization",
    "purchaser", "buyer", "alternate id", "file attachments",
}


def promote_header(header: dict[str, Any]) -> dict[str, str]:
    """The header fields that get columns of their own.

    Returns all three keys every time, empty when the portal published nothing
    for them — a bid missing a fiscal year is a blank cell, not a missing column.
    """
    found = {field: "" for field in HEADER_ALIASES}
    normalised = {normalise(label): str(value).strip()
                  for label, value in (header or {}).items()}

    for field, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            # Exact first, so "bid type" does not lose to "bid type code" when
            # both are on the page.
            if normalised.get(alias):
                found[field] = normalised[alias]
                break
        if found[field]:
            continue
        for alias in aliases:
            match = next((v for k, v in normalised.items() if alias in k and v), "")
            if match:
                found[field] = match
                break
    return found


def additional_header(header: dict[str, Any]) -> str:
    """Everything else in the header table, as one readable cell.

    `Label: value` pairs separated by a bullet, in the order the portal
    published them. This is what replaces the JSON file: the same completeness,
    in a cell someone can read without a tool.
    """
    promoted = {
        normalise(alias)
        for aliases in HEADER_ALIASES.values() for alias in aliases
    }
    parts = []
    for label, value in (header or {}).items():
        key = normalise(label)
        if key in promoted or key in _UNINTERESTING:
            continue
        text = " ".join(str(value).split())
        if text:
            parts.append(f"{str(label).strip().rstrip(':')}: {text}")
    return " • ".join(parts)


# -- the item sheet -----------------------------------------------------------

#: The item fields the text file prints, and the heading each gets. Anything an
#: item carries beyond these is printed after them under its own label, so a
#: column the city adds shows up rather than being dropped.
ITEM_FIELDS: list[tuple[str, str]] = [
    ("name", "Item Name"),
    ("quantity", "Quantity"),
    ("unit", "Unit of Measure"),
    ("unit_price", "Unit Price"),
    ("nigp_code", "NIGP Code"),
    ("commodity_code", "Commodity Code"),
    ("specification", "Specification Details"),
]

_RULE = "=" * 70
_THIN = "-" * 70


def render_items_text(record: dict[str, Any]) -> str:
    """`bid_items_details.txt` for one bid.

    Always returns a document, even for a bid with no line items: a folder that
    contains attachments and nothing explaining them is worse than one holding a
    page that says the portal published no item breakdown. The header block is
    printed either way, so the file identifies its own bid.
    """
    items = record.get("items") or []
    lines = [
        _RULE,
        "BID ITEM SPECIFICATIONS".center(70),
        _RULE,
        f"Bid Number: {record.get('bid_number') or '—'}",
        f"Title: {record.get('description') or '—'}",
    ]

    # The promoted header fields, so the file stands on its own away from the
    # spreadsheet — printed only when the portal published them.
    for field, heading in (
        ("organization", "Organization"),
        ("buyer", "Buyer"),
        ("bid_opening_date", "Bid Opening Date"),
        ("fiscal_year", "Fiscal Year"),
        ("solicitation_type", "Procurement / Solicitation Type"),
        ("pre_bid_conference", "Pre-Bid Conference"),
    ):
        value = " ".join(str(record.get(field) or "").split())
        if value:
            lines.append(f"{heading}: {value}")

    lines += [_THIN, "", "ITEM DETAILS & LINE ITEMS:", ""]

    if not items:
        lines += [
            "  The portal published no line-item breakdown for this bid.",
            "  Any specifications are in the attached documents in this folder.",
            "",
        ]
    for position, item in enumerate(items, start=1):
        lines.append(f"Item #{item.get('item_number') or position}:")
        printed = set()
        for field, heading in ITEM_FIELDS:
            value = " ".join(str(item.get(field) or "").split())
            printed.add(field)
            if value:
                lines.append(f"  - {heading}: {value}")
        # Anything the page carried that this file does not have a heading for.
        for key, value in item.items():
            if key in printed or key == "item_number":
                continue
            text = " ".join(str(value or "").split())
            if text:
                lines.append(f"  - {str(key).replace('_', ' ').title()}: {text}")
        lines.append("")

    lines.append(_RULE)
    return "\n".join(lines) + "\n"
