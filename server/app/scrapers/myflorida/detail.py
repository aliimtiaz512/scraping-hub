"""Reading one advertisement's detail page (`/vendor/ads/detail/…`).

The page is Angular Material, so the markup is a wrapper soup with almost no
stable hooks — every element carries an `_ngcontent-ehp-c286` scope attribute
that the portal regenerates on each build, and the layout classes (`flex-fill`,
`f-sm`, `mat-headline`) are shared by unrelated elements. Three things *are*
stable and everything here is anchored on one of them:

  ids            `#topBar`, `#mainSection` — hand-written by the portal, not
                 generated, and unchanged across every capture we have.
  component tags `<mfmp-commodity-codes-list>` — the Angular component's own
                 selector, which is the portal's public name for that table.
  visible labels "Advertisement Number:", "Name:", "Advertisement Status:" —
                 what a person reads off the page, so the thing most likely to
                 survive a rewrite of the markup under it.

Class selectors are used only where the class is the *only* distinguishing mark
(the advertisement type is a `span.mat-headline.f-sm`, the agency the plain
`span.f-sm` after the `h1`), and never on their own — each is scoped to the
title block so a `f-sm` somewhere else on the page cannot be mistaken for it.

Two shapes of labelled text, handled differently:

    <div>Advertisement Number: AD-16589</div>     one node, "label: value"
    <span>Name:</span><span> Kim Whitwam </span>  label and value are siblings

The first is read off the innermost elements of `#topBar` — innermost because
the topBar's divs nest three deep and an outer one's text is its children's run
together with no separator ("AD-16589Agency Advertisement Number:"). The second
is read per contact row, joining the row's spans with a space first.

Parsing is BeautifulSoup over `driver.page_source` rather than Selenium element
lookups: a detail page is a single static document, so one parse of the whole
thing is faster than a dozen round trips and immune to the staleness that
per-element traversal invites. It is also what makes this testable against a
saved page — see `server/tests/test_myflorida_detail.py`.

**Nothing here judges an advertisement.** No score, no verdict, no accept or
reject: every field is what the portal said, and deciding what is worth
pursuing is the reviewer's job.
"""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# `#topBar` label -> record field. Matched after normalisation (lowercased,
# non-alphanumerics dropped), so "Published Date/Time" and "Published Date"
# both land on the same key.
TOP_BAR_FIELDS: dict[str, str] = {
    "advertisementnumber": "ad_number",
    "agencyadvertisementnumber": "agency_ad_number",
    "versionnumber": "version",
    "publisheddatetime": "published_date",
    "startdatetime": "open_date",
    "enddatetime": "close_date",
    "responsesopendatetime": "responses_open_date",
}

# The two labelled lines that sit in the title block above `<mfmp-bid-detail>`
# rather than in the topBar.
HEADER_FIELDS: dict[str, str] = {
    "advertisementstatus": "status",
    "lasteditdatetime": "last_edit_date",
}

# Contact row label -> record field, in the "Please direct all questions to:"
# section. Address is captured too: an out-of-state address is often the first
# sign that an advertisement is not what its title suggests.
CONTACT_FIELDS: dict[str, str] = {
    "name": "contact_name",
    "phone": "contact_phone",
    "email": "contact_email",
    "address": "contact_address",
}

# Every field `parse` promises, so a caller can rely on the keys existing even
# for a page that rendered none of them.
FIELDS: tuple[str, ...] = (
    "ad_number", "agency_ad_number", "version", "title", "ad_type", "agency",
    "status", "open_date", "close_date", "published_date", "responses_open_date",
    "last_edit_date", "commodity_codes", "contact_name", "contact_email",
    "contact_phone", "contact_address", "description", "detail_url",
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Block-level tags, used to split the advertisement body into lines. Inline
# tags (<strong>, <a>, <span>) are deliberately absent: they sit *inside* a
# sentence and must not break one.
_BLOCK_TAGS = ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "pre")

# What separates a commodity code from its description, and one pair from the
# next. A pipe rather than a comma between pairs: descriptions contain commas
# ("Hearing aid, external"), and a reader splitting the cell on a comma would
# quietly get the wrong count.
CODE_JOIN = " — "
PAIR_JOIN = " | "


def _normalise(label: str) -> str:
    return _NON_ALNUM.sub("", (label or "").lower())


def _text(node) -> str:
    """A node's visible text, with the portal's whitespace collapsed.

    `&nbsp;` matters here: the End Date/Time line is "End Date/Time: \xa0…" and
    a plain `.strip()` leaves the non-breaking space glued to the front of the
    date. It is turned into an ordinary space before the collapse, which is what
    a browser's innerText would have given us.
    """
    if node is None:
        return ""
    return " ".join(node.get_text(" ", strip=True).replace("\xa0", " ").split())


def _leaf_lines(root) -> list[str]:
    """One line per innermost element under `root`.

    Innermost = has no descendant element of its own. `#topBar` nests its divs
    three deep, so taking every div would read each value once per ancestor,
    with the labels of the siblings run together into it.
    """
    if root is None:
        return []
    lines = []
    for element in root.find_all(True):
        if element.find(True) is not None:
            continue
        text = _text(element)
        if text:
            lines.append(text)
    return lines


def _split_label(line: str) -> tuple[str, str] | None:
    """"Advertisement Number: AD-16589" -> ("advertisementnumber", "AD-16589").

    Split on the *first* colon only: values carry colons of their own (a time,
    "Re: …"), and splitting on the last would take the label with them.
    """
    if ":" not in line:
        return None
    label, _, value = line.partition(":")
    return _normalise(label), value.strip()


def _read_labelled(root, mapping: dict[str, str]) -> dict[str, str]:
    """Fields from `root`'s "label: value" lines, keyed per `mapping`.

    First value wins: `#mainSection` repeats "Advertisement Number:" inside the
    ad body, and a later line must not overwrite what the topBar stated.
    """
    out: dict[str, str] = {}
    for line in _leaf_lines(root):
        parsed = _split_label(line)
        if parsed is None:
            continue
        label, value = parsed
        field = mapping.get(label)
        if field and value and field not in out:
            out[field] = value
    return out


def _title_block(soup):
    """The header above `<mfmp-bid-detail>` holding type, title, agency, status.

    `#titleContainer` is the portal's own id for it. The `h1`'s parent is the
    fallback: the block is one div containing exactly those elements, so if the
    id is renamed the `h1` still leads back to it.
    """
    container = soup.find(id="titleContainer")
    if container is not None:
        return container
    heading = soup.find("h1", class_="mat-headline")
    return heading.parent if heading is not None else None


def _commodity_codes(soup) -> list[tuple[str, str]]:
    """(code, description) pairs from the commodity table, in page order.

    Scoped to `<mfmp-commodity-codes-list>` and not to the column classes alone:
    the Downloadable Files table further down the page uses the same
    `.mat-column-description`, and matching on the class would mix attachment
    names into the codes.
    """
    component = soup.find("mfmp-commodity-codes-list")
    if component is None:
        return []
    pairs: list[tuple[str, str]] = []
    for row in component.select("tbody tr"):
        code = _text(row.select_one(".mat-column-code"))
        description = _text(row.select_one(".mat-column-description"))
        if code or description:
            pairs.append((code, description))
    return pairs


def _contacts(soup) -> dict[str, str]:
    """The "Please direct all questions to:" block.

    Each row is `<div><span>Label:</span><span>value</span></div>`, so the label
    and its value are separate nodes and `_leaf_lines` would give them as two
    lines. The row's own text — spans joined with a space — puts them back on
    one line, which `_split_label` then reads like any other.

    The section is found by its heading rather than by position: it is the last
    section on some ads and the second-to-last on others, and an ad with no
    attachments table shifts every index.
    """
    heading = soup.find(
        lambda tag: tag.name in ("h1", "h2", "h3")
        and "direct all questions" in _text(tag).lower()
    )
    section = heading.parent if heading is not None else None
    if section is None:
        return {}

    out: dict[str, str] = {}
    for row in section.find_all("div", recursive=False):
        parsed = _split_label(_text(row))
        if parsed is None:
            continue
        label, value = parsed
        field = CONTACT_FIELDS.get(label)
        if field and value and field not in out:
            out[field] = value

    # The address in the DOM is three text nodes with an Angular placeholder
    # between them ("207 San Marco Ave.", "St. Augustine,", "FL 32084"); joining
    # on a space leaves the comma spacing a person would write.
    link = section.select_one('a[href^="mailto:"]')
    if link is not None:
        # The href is the authoritative address — the anchor's own text is
        # sometimes truncated with an ellipsis on a long one.
        out["contact_email"] = link["href"].removeprefix("mailto:").strip()
    return out


def _description(soup) -> str:
    """The advertisement body from `#mainSection`, one line per paragraph.

    Read per block element rather than as one `get_text("\\n")` run. The naive
    reading breaks a line at every inline tag, so "Single Source Award to:
    <strong>Cochlear Americas</strong>" arrives as two lines and the sentence
    is cut in half — the paragraph breaks are the only structure this text has
    and they are worth getting right.

    The portal nests `<p>` inside `<p>`, which is invalid HTML; the parser
    unnests them into siblings the way a browser does, so the inner paragraphs
    are what this walk finds. The `&nbsp;` spacer paragraphs the portal puts
    between sections come out empty and are dropped.
    """
    section = soup.find(id="mainSection")
    if section is None:
        return ""
    blocks = [
        _text(element)
        for element in section.find_all(_BLOCK_TAGS)
        if element.find(_BLOCK_TAGS) is None
    ]
    lines = [line for line in blocks if line]
    if not lines:
        # A body with no block markup at all — take it whole rather than blank.
        lines = [_text(section)]
    return "\n".join(lines).strip()


def parse(html: str, page_url: str = "") -> dict[str, str]:
    """Every field the detail page carries, as a flat record.

    Always returns all of `FIELDS`; anything the page did not render comes back
    as an empty string rather than missing, so a caller never has to guard a
    lookup and a row never shifts a column because one ad lacked a contact.
    """
    soup = BeautifulSoup(html or "", "lxml")
    record: dict[str, str] = {field: "" for field in FIELDS}

    header = _title_block(soup)
    if header is not None:
        record["ad_type"] = _text(header.select_one("span.mat-headline.f-sm"))
        record["title"] = _text(header.select_one("h1.mat-headline"))
        # The agency is the plain `f-sm` span — the advertisement type above it
        # carries `mat-headline` as well, so the class alone matches both.
        agency = next(
            (
                span for span in header.select("span.f-sm")
                if "mat-headline" not in (span.get("class") or [])
            ),
            None,
        )
        record["agency"] = _text(agency)
        record.update(_read_labelled(header, HEADER_FIELDS))

    record.update(_read_labelled(soup.find(id="topBar"), TOP_BAR_FIELDS))

    pairs = _commodity_codes(soup)
    record["commodity_codes"] = PAIR_JOIN.join(
        CODE_JOIN.join(part for part in pair if part) for pair in pairs
    )

    record.update(_contacts(soup))
    record["description"] = _description(soup)
    record["detail_url"] = page_url or ""
    return record
