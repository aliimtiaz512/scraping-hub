"""MFMP's advertisement detail page: every field the summary sheet takes off it.

The fixture is a real detail page (AD-16589, an Agency Decision from the Florida
School for the Deaf and the Blind) captured as the browser rendered it, Angular
scope attributes and comment placeholders intact. What these pin down is that
each summary column the parser feeds has a source that actually resolves, and
the four readings that took a second attempt to get right:

  * the End Date's leading `&nbsp;`, which a plain strip leaves on the value
  * `#topBar`'s three-deep nesting, which reads every value once per ancestor
    if the walk is not limited to the innermost elements
  * the agency and the advertisement type, which share the `f-sm` class
  * the ad body's inline `<strong>`, which breaks a sentence in two if the text
    is read with a newline at every tag boundary

    server/.venv/bin/python -m pytest server/tests/test_myflorida_detail.py
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.scrapers.myflorida import detail  # noqa: E402
from app.scrapers.myflorida.workbook import RECORD_COLUMNS  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "mfmp_bid_detail.html"
PAGE_URL = "https://vendor.myfloridamarketplace.com/vendor/ads/detail/16589"


@pytest.fixture(scope="module")
def parsed():
    return detail.parse(FIXTURE.read_text(encoding="utf-8"), PAGE_URL)


# -- the identifiers in #topBar ----------------------------------------------


def test_the_three_numbers_are_read_apart_from_each_other(parsed):
    """The topBar nests its divs three deep. Read carelessly, the outer div's
    text is its children run together — "AD-16589Agency Advertisement Number:"."""
    assert parsed["ad_number"] == "AD-16589"
    assert parsed["agency_ad_number"] == "IA-27-038"
    assert parsed["version"] == "1"


def test_the_ad_body_does_not_overwrite_the_top_bar(parsed):
    """`#mainSection` says "Advertisement Number: IA-27-038" in its own text —
    the agency's number, under the portal's label for the portal's number. The
    topBar stated it first and has to win."""
    assert "Advertisement Number: IA-27-038" in parsed["description"]
    assert parsed["ad_number"] == "AD-16589"


# -- the title block ----------------------------------------------------------


def test_the_type_title_and_agency_are_told_apart(parsed):
    """All three sit in one div, and the type and the agency are both `f-sm` —
    only the type's extra `mat-headline` separates them."""
    assert parsed["ad_type"] == "Agency Decision"
    assert parsed["title"] == "Intent to Award Single Source provider to Cochlear Americas"
    assert parsed["agency"] == "Florida School for the Deaf and the Blind (FSDB)"


def test_the_status_comes_off_its_label(parsed):
    """The one field with no column in the results grid, which is why the
    summary's Status has to come from here."""
    assert parsed["status"] == "OPEN"


# -- the dates ----------------------------------------------------------------


def test_every_date_the_page_carries(parsed):
    assert parsed["published_date"] == "08/05/2026 12:32 AM"
    assert parsed["open_date"] == "08/26/2026 01:30 AM"
    assert parsed["responses_open_date"] == "08/29/2026 01:30 AM"
    assert parsed["last_edit_date"] == "08/05/2026, 12:32 AM"


def test_the_published_date_is_not_the_start_date(parsed):
    """Worth its own column only because the two genuinely differ — this ad was
    posted three weeks before its window opens."""
    assert parsed["published_date"] != parsed["open_date"]


def test_the_end_date_loses_its_non_breaking_space(parsed):
    """The markup is `End Date/Time: &nbsp;08/29/2026` — a plain strip leaves
    the \\xa0 glued to the front of the date and every cell reads wrong."""
    assert parsed["close_date"] == "08/29/2026 01:30 AM"
    assert "\xa0" not in parsed["close_date"]


# -- commodity codes ----------------------------------------------------------


def test_each_code_stays_attached_to_its_own_description(parsed):
    """Two comma-joined lists would lose the pairing the moment a description
    contained a comma. One cell, pairs joined by a pipe."""
    assert parsed["commodity_codes"] == (
        "42211705 — Hearing aid | 45111701 — Assistive listening devices"
    )


def test_the_attachments_table_is_not_mistaken_for_commodity_codes():
    """Both tables use `.mat-column-description`. Scoping to the commodity
    component is the only thing keeping the attachment's name out of the cell."""
    parsed = detail.parse(FIXTURE.read_text(encoding="utf-8"))
    assert "Intent to Award" not in parsed["commodity_codes"]


# -- point of contact ---------------------------------------------------------


def test_the_contact_is_read_across_its_split_label_and_value(parsed):
    """Each row is `<span>Name:</span><span>value</span>` — label and value are
    different nodes, unlike every other labelled field on the page."""
    assert parsed["contact_name"] == "Kim Whitwam"
    assert parsed["contact_phone"] == "(904) 827-2356"
    assert parsed["contact_email"] == "whitwamk@fsdbk12.org"


def test_the_address_survives_the_placeholder_between_its_lines(parsed):
    """Angular splits the city out into its own span with comment placeholders
    on either side, so the address is three text nodes and not one."""
    assert parsed["contact_address"] == "207 San Marco Ave. St. Augustine, FL 32084"


def test_the_email_comes_from_the_href_not_the_link_text(parsed):
    """The anchor's text is truncated on a long address; the mailto is not."""
    assert parsed["contact_email"] == "whitwamk@fsdbk12.org"
    assert "mailto" not in parsed["contact_email"]


# -- the advertisement body ---------------------------------------------------


def test_an_inline_tag_does_not_break_a_sentence(parsed):
    """"Single Source Award to: <strong>Cochlear Americas</strong>" is one line.
    Reading the section with a newline at every tag boundary makes it two."""
    assert "Single Source Award to: Cochlear Americas" in parsed["description"]


def test_the_paragraphs_are_kept_apart(parsed):
    """The portal nests <p> inside <p>, which is invalid; the parser unnests
    them the way a browser does and each becomes its own line."""
    lines = parsed["description"].splitlines()
    assert "Commodities:" in lines
    assert "45111701 – Assistive Listening Devices" in lines
    # The &nbsp; spacer paragraphs between sections are not lines of their own.
    assert all(line.strip() for line in lines)


def test_the_statutory_notice_is_kept_whole(parsed):
    """The longest paragraph on the page, and the one a reviewer most needs —
    it says the ad is not a competitive solicitation at all."""
    assert "THIS IS NOT A COMPETITIVE SOLICITATION" in parsed["description"]
    assert "§287.057(5)(c), F.S." in parsed["description"]


# -- the contract with the summary sheet --------------------------------------


def test_the_detail_url_is_what_the_caller_passed(parsed):
    """The results grid's Number cell has no href — it links through a JS click
    handler — so the address only exists once the route has resolved."""
    assert parsed["detail_url"] == PAGE_URL


def test_every_column_the_sheet_fills_from_here_has_a_value(parsed):
    """The nine columns with no other source. A regression in any one of them
    would otherwise show up as a quietly blank column in a delivered workbook."""
    for key in (
        "ad_number", "agency_ad_number", "version", "title", "ad_type", "agency",
        "status", "published_date", "commodity_codes", "contact_name",
        "contact_email", "contact_phone", "description",
    ):
        assert parsed[key], f"{key} came back empty"


def test_a_page_that_will_not_parse_still_returns_every_field():
    """A row must never shift a column because one ad rendered nothing."""
    parsed = detail.parse("<html><body>nothing here</body></html>", "https://x/detail/1")

    assert set(parsed) == set(detail.FIELDS)
    assert all(value == "" for key, value in parsed.items() if key != "detail_url")


def test_the_parser_and_the_sheet_agree_on_field_names():
    """The sheet reads its cells straight off the parser's record, so a renamed
    field would silently blank a column rather than fail.

    Three columns are not the parser's: `document_count` is computed from the
    saved attachments, and the evaluation columns come from
    `myflorida/evaluation.py`. They are named here rather than excluded by a
    wildcard, so a *fourth* unaccounted column still fails this test.
    """
    not_from_the_parser = {"document_count", "decision", "evaluation_reason", "ai_notes"}
    sheet_keys = {key for key, _ in RECORD_COLUMNS} - not_from_the_parser

    assert sheet_keys <= set(detail.FIELDS)
