"""The open list's client-side render, and telling "not yet" from "nothing".

Bonfire builds the opportunities table in the browser: the pane ships in the
server's markup, DataTables puts a row in the table the moment it initialises,
and the opportunities themselves arrive later over AJAX. So both of the obvious
waits — "the pane exists", "a row exists" — are satisfied while the table is
still empty, and reading it then yields the loading row, which carries no
reference and no link and is discarded exactly like the empty-state placeholder.

That failure is silent: the wait succeeds, the read succeeds, and an agency with
live bids is reported as having none. These tests pin the distinction.

    server/.venv/bin/python -m pytest server/tests/test_ridemetro_hydration.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from selenium.common.exceptions import StaleElementReferenceException  # noqa: E402
from selenium.webdriver.common.by import By  # noqa: E402

from app.scrapers.ridemetro import opportunities  # noqa: E402

HEADERS = ["Status", "Ref. #", "Project", "Close Date", "Days Left", "Action"]


# -- stubs -------------------------------------------------------------------


class FakeEl:
    def __init__(self, text="", displayed=True, links=(), stale=False):
        self._text = text
        self._displayed = displayed
        self._links = list(links)
        self._stale = stale

    def get_attribute(self, name):
        if self._stale:
            raise StaleElementReferenceException("gone")
        return self._text if name == "textContent" else None

    @property
    def text(self):
        return self._text

    def is_displayed(self):
        return self._displayed

    def find_elements(self, by, value):
        return self._links


class FakeRow:
    """A <tr>. `cells` are the <td>s; a row-level anchor search flattens them."""

    def __init__(self, cells, stale=False):
        self._cells = cells
        self._stale = stale

    def find_elements(self, by, value):
        if self._stale:
            raise StaleElementReferenceException("gone")
        if value == "td":
            return self._cells
        return [link for cell in self._cells for link in cell._links]


class FakePage:
    """Serves each selector in `opportunities.SEL` from an explicit bucket, so a
    test states the page's condition rather than a pile of HTML."""

    def __init__(self, *, rows=(), processing=(), empty_cells=(), tables=(), frames=()):
        self._by_selector = {
            opportunities.SEL["rows"][1]: list(rows),
            opportunities.SEL["header_cells"][1]: [FakeEl(h) for h in HEADERS],
            opportunities.SEL["processing"][1]: list(processing),
            opportunities.SEL["empty_cell"][1]: list(empty_cells),
            opportunities.SEL["table"][1]: list(tables),
            opportunities.SEL["frames"][1]: list(frames),
        }

    def find_elements(self, by, value):
        assert by == By.CSS_SELECTOR, by
        return self._by_selector.get(value, [])


def data_row(ref="IFB 2026000025", url="https://ridemetro.bonfirehub.com/opportunities/246231"):
    cells = [FakeEl("Open"), FakeEl(ref), FakeEl("Auxiliary Power Supply Parts"),
             FakeEl("Aug 19th 2026, 2:00 PM CDT"), FakeEl("14")]
    cells.append(FakeEl("View Opportunity", links=[FakeEl(url)]))
    return FakeRow(cells)


def placeholder_row(text):
    """The one full-width cell DataTables puts in a rowless table — the same
    element whether it is loading or settled, which is the whole problem."""
    return FakeRow([FakeEl(text)])


# -- the states --------------------------------------------------------------


def test_a_rendered_opportunity_is_ready():
    page = FakePage(rows=[data_row()], tables=[FakeEl()])
    assert opportunities.hydration_state(page) == opportunities.READY


def test_loading_text_outranks_empty_wording_in_the_same_cell():
    """The placeholder cell is the same element in both states, and a portal
    that says "Loading, no records yet" must not settle on the second half."""
    text = "Loading — no records yet"
    page = FakePage(rows=[placeholder_row(text)], empty_cells=[FakeEl(text)],
                    tables=[FakeEl()])
    assert opportunities.hydration_state(page) == opportunities.LOADING


def test_the_loading_row_is_not_mistaken_for_an_empty_table():
    """The bug this module exists for. DataTables' loading row has no reference
    and no link, so `read_rows` drops it exactly as it drops the empty-state
    placeholder — and the agency reports zero opportunities it actually has."""
    loading = placeholder_row("Loading…")
    page = FakePage(rows=[loading], empty_cells=[FakeEl("Loading…")], tables=[FakeEl()])

    assert opportunities.hydration_state(page) == opportunities.LOADING
    # …and this is what reading it too early would have produced.
    assert opportunities.read_rows(page) == []


def test_the_settled_empty_state_is_not_mistaken_for_loading():
    row = placeholder_row("There are no open projects at this time.")
    page = FakePage(
        rows=[row],
        empty_cells=[FakeEl("There are no open projects at this time.")],
        tables=[FakeEl()],
    )
    assert opportunities.hydration_state(page) == opportunities.EMPTY


def test_other_empty_state_wordings_settle_too():
    """Bonfire, DataTables' defaults and individual agencies each word this
    differently. An unrecognised phrasing is not a loud failure — it reads as
    "still loading" until the deadline — so the vocabulary is deliberately
    broad."""
    for text in (
        "No data available in table",
        "No matching records found",
        "There are no open opportunities.",
        "No active solicitations",
        "There are no current solicitations.",
        "0 records found",
        "No bids found",
        "Nothing to display",
        "None found",
        "No records",
    ):
        page = FakePage(rows=[placeholder_row(text)], empty_cells=[FakeEl(text)],
                        tables=[FakeEl()])
        assert opportunities.hydration_state(page) == opportunities.EMPTY, text


def test_empty_state_wording_does_not_fire_on_real_project_titles():
    """The pattern is broad, so it is only ever matched against the placeholder
    cell — but the wording still has to survive titles that start with "No"."""
    for text in (
        "No. 5 Bridge Repair",
        "Nonstop Emergency Generator Project",
        "Notice of Intent — Records Management System",
    ):
        page = FakePage(rows=[data_row(ref=text)], tables=[FakeEl()])
        assert opportunities.hydration_state(page) == opportunities.READY, text


def test_a_visible_processing_banner_outranks_everything():
    """DataTables leaves stale rows on screen while it re-fetches, so rows that
    look real can belong to the previous request."""
    page = FakePage(
        rows=[data_row()],
        processing=[FakeEl("Processing...", displayed=True)],
        tables=[FakeEl()],
    )
    assert opportunities.hydration_state(page) == opportunities.LOADING


def test_the_processing_banner_is_judged_by_visibility_not_presence():
    """It is in the DOM from the start and toggled by display — presence alone
    would mean the state never leaves LOADING."""
    page = FakePage(
        rows=[data_row()],
        processing=[FakeEl("Processing...", displayed=False)],
        tables=[FakeEl()],
    )
    assert opportunities.hydration_state(page) == opportunities.READY


def test_a_table_that_does_not_exist_yet_is_still_loading():
    """Before DataTables initialises there is no table and no row to read, which
    is not the same as an empty result."""
    assert opportunities.hydration_state(FakePage()) == opportunities.LOADING


def test_a_built_but_rowless_table_is_idle_not_loading():
    """A portal with no open bids that renders no placeholder at all. Nothing is
    coming, but the page never says so — so it is not EMPTY on this evidence,
    and it is not LOADING either. Waiting the full budget for a placeholder that
    will never arrive is what used to turn an empty portal into a timeout."""
    assert opportunities.hydration_state(FakePage(tables=[FakeEl()])) == opportunities.IDLE


def test_rows_that_say_nothing_are_idle_too():
    """Rows present, none of them opportunities, no placeholder text explaining
    why. Same ambiguity, same answer: not empty on this evidence, but not a
    reason to keep waiting forever either."""
    page = FakePage(rows=[placeholder_row("")], empty_cells=[FakeEl("")], tables=[FakeEl()])
    assert opportunities.hydration_state(page) == opportunities.IDLE


def test_a_table_being_rewritten_reads_as_loading():
    page = FakePage(rows=[FakeRow([], stale=True)], tables=[FakeEl()])
    assert opportunities.hydration_state(page) == opportunities.LOADING


def test_ready_agrees_with_what_read_rows_will_return():
    """The two must apply the same test for "a real row", or the wait can pass
    on a row the read then discards."""
    page = FakePage(rows=[data_row(), placeholder_row("Loading…")], tables=[FakeEl()])
    assert opportunities.hydration_state(page) == opportunities.READY
    records = opportunities.read_rows(page)
    assert [r["ref_number"] for r in records] == ["IFB 2026000025"]


# -- which tabs the portal offers --------------------------------------------


def tab(label, tab_id):
    el = FakeEl(label)
    el._id = tab_id
    return el


class FakePortal(FakePage):
    """A page that also answers the tab selectors."""

    def __init__(self, *, tabs=(), open_tab=(), **kw):
        super().__init__(**kw)
        self._by_selector[opportunities.SEL["tabs"][1]] = list(tabs)
        self._by_selector[opportunities.SEL["open_tab"][1]] = list(open_tab)


def test_the_session_portal_offers_no_open_tab():
    """What "Go to Agency" opens for an agency the SPA has no portal URL for:
    the region session portal, which opens on My Opportunities and has no Open
    Public Opportunities tab at all."""
    page = FakePortal(tabs=[FakeEl("My Opportunities")], open_tab=[])
    assert opportunities.has_open_tab(page) is False
    assert opportunities.offered_tabs(page) == ["My Opportunities"]


def test_an_agency_portal_offers_the_open_tab():
    page = FakePortal(
        tabs=[FakeEl("Open Public Opportunities"), FakeEl("Past Public Opportunities"),
              FakeEl("My Opportunities")],
        open_tab=[FakeEl()],
    )
    assert opportunities.has_open_tab(page) is True
    assert "Open Public Opportunities" in opportunities.offered_tabs(page)


def test_tab_labels_are_deduplicated_and_blanks_dropped():
    """The selector matches the tab and its <li> wrapper, so the same label
    arrives twice; an unrendered tab arrives empty."""
    page = FakePortal(tabs=[FakeEl("My Opportunities"), FakeEl("My Opportunities"),
                            FakeEl("")])
    assert opportunities.offered_tabs(page) == ["My Opportunities"]


def test_a_portal_that_has_not_drawn_its_tabs_reports_none():
    """The distinction the verdict turns on: no tabs at all means "we cannot
    tell yet", not "this portal has no public list". Calling the second one
    settled would drop a real agency's bids with no retry and no error."""
    assert opportunities.offered_tabs(FakePortal()) == []
    assert opportunities.has_open_tab(FakePortal()) is False


# -- frames ------------------------------------------------------------------


def test_frames_are_reported_when_present():
    """No portal seen so far embeds the table, so there is no frame-switching
    layer — but a page that does must be named as such rather than failing as an
    ordinary selector timeout."""
    assert opportunities.has_frames(FakePage()) is False
    assert opportunities.has_frames(FakePage(frames=[FakeEl()])) is True
