"""Reading one buy's detail page (`/fbweb/buyDetails.do?buy_id=…`).

The page is a run of sections, each a `div.title > h2` heading followed by a
sibling table:

    <div class="title titleBorder"><h2>General Buy Information</h2></div>
    <table>…</table>
    <div class="title titleBorder"><h2>Line Item(s)</h2></div>
    <table class="tableRed">…</table>
    …

So the parser walks the headings and takes each one's next table, rather than
indexing tables by position: a buy with no attachments, or no shipping row, or a
section the portal adds later, shifts every position but no heading.

One heading needs an exact match rather than a contains: **"Line Item(s)
Template - Optional"** sits immediately before **"Line Item(s)"** and holds a
"Download Template" button, not the items.

Parsing is done in BeautifulSoup over `driver.page_source`, not through
Selenium element lookups — a detail page is a single static document, and one
parse of the whole thing is both faster and immune to the staleness that
per-element traversal invites.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Section headings, as they appear in the page's <h2>.
GENERAL_INFO = "General Buy Information"
LINE_ITEMS = "Line Item(s)"
LINE_ITEMS_TEMPLATE = "Line Item(s) Template - Optional"
BIDDING_REQUIREMENTS = "Bidding Requirements"
BUY_TERMS = "Buy Terms"
SHIPPING = "Shipping Information"
SELLER_ATTACHMENTS = "Seller Attachment(s): Required"
BUY_ATTACHMENTS = "Buy Attachment(s)"

# General Information label -> model field. Labels are matched after
# normalisation (lowercased, punctuation stripped), so "Buy #:" and "Buy#"
# both land on the same key; anything unmapped is kept in `extra`.
GENERAL_INFO_FIELDS: dict[str, str] = {
    "buy": "buy_number",
    "solicitation": "solicitation_number",
    "buydescription": "buy_description",
    "category": "category",
    "subcategory": "subcategory",
    "naics": "naics",
    "naicscodesizestandard": "naics_size_standard",
    "samcontractopportunity": "sam_contract_opportunity",
    "setasiderequirement": "set_aside",
    "buyer": "buyer",
    "enddate": "end_date",
    "endtime": "end_time",
    "sellerquestiondeadline": "seller_question_deadline",
    "delivery": "delivery",
    "repostreason": "repost_reason",
}

# The standing note the portal appends to every Repost Reason cell. It is boiler-
# plate about the field, not a reason, and it is longer than any real value.
_REPOST_NOTE = "Please Note: Repost Reason is provided as a courtesy only"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalise(label: str) -> str:
    return _NON_ALNUM.sub("", (label or "").lower())


def _text(node) -> str:
    """A node's visible text, with the portal's generous whitespace collapsed."""
    if node is None:
        return ""
    return re.sub(r"[ \t\xa0]+", " ", node.get_text(" ", strip=True)).strip()


def _section_table(soup: BeautifulSoup, heading: str):
    """The table belonging to `heading`, or None if the section isn't present."""
    for title in soup.select("div.title"):
        h2 = title.find("h2")
        if h2 is None or _text(h2) != heading:
            continue
        table = title.find_next("table")
        if table is not None:
            return table
    return None


def parse_general_info(soup: BeautifulSoup) -> dict[str, Any]:
    """The General Buy Information `th`/`td` pairs.

    Mapped labels become model fields; unmapped ones are kept under `extra` so a
    field the portal adds later is recorded rather than silently dropped.
    """
    table = _section_table(soup, GENERAL_INFO)
    if table is None:
        return {}

    info: dict[str, Any] = {"extra": {}}
    for row in table.find_all("tr"):
        header, value = row.find("th"), row.find("td")
        if header is None or value is None:
            continue
        label = _text(header).rstrip(":")
        text = _text(value)
        if label.lower().startswith("repost reason"):
            text = text.split(_REPOST_NOTE)[0].strip()
        field = GENERAL_INFO_FIELDS.get(_normalise(label))
        if field:
            info[field] = text
        elif label:
            info["extra"][label] = text
    return info


def parse_line_items(soup: BeautifulSoup) -> list[dict[str, str]]:
    """The Line Item(s) rows: item number, description, quantity, unit.

    Matched on the exact heading — see the module docstring on the neighbouring
    "Line Item(s) Template - Optional" section.
    """
    table = _section_table(soup, LINE_ITEMS)
    if table is None:
        return []

    items: list[dict[str, str]] = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue  # the header row, which is <th>
        items.append({
            "no": _text(cells[0]),
            "description": _text(cells[1]),
            "qty": _text(cells[2]),
            "unit": _text(cells[3]),
        })
    return items


def parse_bidding_requirements(soup: BeautifulSoup) -> list[dict[str, str]]:
    """The Bidding Requirements rows, as label -> instruction.

    Each row is `<div class="biLabel"><label>Open Market:</label></div><div>…`.
    """
    table = _section_table(soup, BIDDING_REQUIREMENTS)
    if table is None:
        return []

    requirements: list[dict[str, str]] = []
    for cell in table.select("td"):
        label_box = cell.find("div", class_="biLabel")
        label = _text(label_box).rstrip(":") if label_box else ""
        text = _text(cell)
        if label and text.startswith(label):
            text = text[len(label):].lstrip(": ").strip()
        if label or text:
            requirements.append({"name": label, "text": text})
    return requirements


def parse_buy_terms(soup: BeautifulSoup) -> list[dict[str, str]]:
    """The Buy Terms rows, as name -> description.

    Routinely tens of thousands of characters of FAR boilerplate. It is kept for
    the record and fed to the evaluator's *body* text only — never to the
    classifier, which it would mislead (the clauses mention maintenance,
    training, audit and R&D in passing).
    """
    table = _section_table(soup, BUY_TERMS)
    if table is None:
        return []

    terms: list[dict[str, str]] = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        name, description = _text(cells[0]), _text(cells[1])
        if name or description:
            terms.append({"name": name, "text": description})
    return terms


def parse_shipping(soup: BeautifulSoup) -> dict[str, str]:
    """The Shipping Information row: city, state, zip.

    The place of performance, and the only structured location on the page. A
    foreign buy commonly fills in the city alone (`Buenos Aires`, no state or
    zip), so an empty state/zip is normal rather than a parse failure.
    """
    table = _section_table(soup, SHIPPING)
    if table is None:
        return {}

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        city, state, _, zip_code = (_text(c) for c in cells[:4])
        if city or state or zip_code:
            return {"city": city, "state": state, "zip": zip_code}
    return {}


def parse_seller_attachments(soup: BeautifulSoup) -> str:
    """What the buyer requires the *seller* to attach to a bid.

    The section leads with a standing paragraph about attachment limits; the
    requirements are the rows after it, and those are what is kept.
    """
    table = _section_table(soup, SELLER_ATTACHMENTS)
    if table is None:
        return ""

    rows = [_text(row) for row in table.find_all("tr")]
    requirements = [
        text for text in rows
        if text and not text.startswith("Sellers are REQUIRED to include")
    ]
    return " | ".join(requirements)


def parse_buy_attachments(soup: BeautifulSoup, page_url: str) -> list[dict[str, str]]:
    """The Buy Attachment(s) rows: document name, absolute URL, size.

    Each name is a real `<a href="/fbweb/viewAtt.do?token=…">`, which is what
    lets the downloader fetch the files over the logged-in session instead of
    driving the browser's download machinery.
    """
    table = _section_table(soup, BUY_ATTACHMENTS)
    if table is None:
        return []

    attachments: list[dict[str, str]] = []
    for row in table.find_all("tr"):
        link = row.find("a", href=True)
        if link is None:
            continue
        cells = row.find_all("td")
        attachments.append({
            "no": _text(cells[0]) if cells else "",
            "name": _text(link),
            "url": urljoin(page_url, link["href"]),
            "size": _text(cells[-1]) if len(cells) >= 3 else "",
        })
    return attachments


def parse(html: str, page_url: str = "") -> dict[str, Any]:
    """Everything one detail page holds, as a single record.

    Sections absent from the page come back empty rather than missing, so a
    caller never has to ask which sections this particular buy had.
    """
    soup = BeautifulSoup(html, "html.parser")
    return {
        "general_info": parse_general_info(soup),
        "line_items": parse_line_items(soup),
        "bidding_requirements": parse_bidding_requirements(soup),
        "buy_terms": parse_buy_terms(soup),
        "shipping": parse_shipping(soup),
        "seller_attachments_required": parse_seller_attachments(soup),
        "attachments": parse_buy_attachments(soup, page_url),
    }


#: What the bid upload count is when the Buy # carries no suffix — a count of
#: none, written as a number rather than left blank, so the column reads as a
#: tally in every row instead of an empty cell the reader has to interpret.
NO_UPLOADS = "0"


def split_buy_number(buy_number: str) -> tuple[str, str]:
    """`"1210780_01"` -> `("1210780_01", "01")`; `"1210980"` -> `(…, "0")`.

    The full identifier is always preserved as the Buy #; the suffix is the
    portal's repost sequence, reported alongside it, and a buy without one
    counts as zero.
    """
    value = (buy_number or "").strip()
    if "_" not in value:
        return value, NO_UPLOADS
    suffix = value.rsplit("_", 1)[1].strip()
    return value, suffix or NO_UPLOADS
