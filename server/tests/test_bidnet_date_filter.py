"""BidNet's date panels: applying a window, and proving it landed.

A run narrowed Published Date to 08/04–08/10/2026 and every keyword went to
zero. The panel reported itself applied — but "applied" only meant no exception
had been raised: each write (`_set_date_input`, `_set_select`, `_click_apply`)
was a silent no-op on a missing element, and nothing read the fields back. So a
window the portal never received and a window it received and honoured produced
the same log line and the same empty export. The run record said
`published_date=RANGE`, which does not even name the dates.

    server/.venv/bin/python -m pytest server/tests/test_bidnet_date_filter.py
"""

import os
import sys

import pytest
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.scrapers.bidnet import sidebar as sidebar_module  # noqa: E402
from app.scrapers.bidnet.filters import DateFilter, SidebarFilterRequest  # noqa: E402
from app.scrapers.bidnet.sidebar import SidebarDriver  # noqa: E402

RANGE = DateFilter(type="RANGE", range_start="08/04/2026", range_end="08/10/2026")

# Captured before the autouse fixture below stubs it out, for the one test that
# exercises the real wait.
_REAL_AWAIT_POSTBACK = SidebarDriver._await_postback


class FakeCheckbox:
    def __init__(self, selected=False):
        self.selected = selected

    def is_selected(self):
        return self.selected


class FakeDriver:
    """Answers the panel's scripts from a dict of element ids it "has"."""

    def __init__(self, present=None, page_state=None, checkbox=True, ticked=True,
                 complaint="", rows=True, replaced=True):
        # None means "every element the panel asks for exists".
        self.present = present
        # The panel's own visible validation message, e.g. "Ending date must be
        # greater or equal to the starting date."
        self.complaint = complaint
        # ISO written into each field's `_hidden` twin, which is what posts.
        self.written_iso: dict[str, str] = {}
        # Whether the page currently has result rows to anchor a staleness wait
        # on, and the anchor node handed out for them.
        self.rows = rows
        self.anchor = object()
        self.anchors_taken: list[object] = []
        # Whether pressing Apply actually causes the portal to replace the
        # results. False models the commandButton ignoring the click — the
        # failure that let unfiltered bids through while the panel reported the
        # window verified. A list is consumed one press at a time.
        self.replaced = replaced
        # A list is consumed one read at a time, so a test can have the page
        # answer differently before and after a retry.
        self.page_state = page_state
        self.checkbox = checkbox
        self.ticked = ticked
        self.written: dict[str, str] = {}
        self.clicked: list[str] = []
        # Ordered trace of everything the panel did, so a test can assert that a
        # postback was waited out *between* the tick and the writes.
        self.events: list[str] = []

    def _has(self, element_id):
        return True if self.present is None else element_id in self.present

    def find_element(self, by, element_id):
        # The panel looks up two different things: its mode checkbox by id, and
        # a results row to use as the staleness anchor. Only the first is a
        # checkbox — answering the anchor lookup with one is what made these
        # fakes accept a wait that never watched anything.
        if by == By.CSS_SELECTOR:
            if not self.rows:
                raise NoSuchElementException("no result rows")
            return self.anchor
        if not self.checkbox:
            raise NoSuchElementException(element_id)
        return FakeCheckbox(selected=self.ticked)

    def _next_state(self):
        if isinstance(self.page_state, list):
            return self.page_state.pop(0) if self.page_state else None
        return self.page_state

    def execute_script(self, script, *args):
        if "mode_checked" in script:                      # the read-back
            self.events.append("read")
            return self._next_state()
        if "panel_" in script:                            # the validation probe
            return self.complaint
        if not args or not isinstance(args[0], str):      # scrollIntoView / tick
            if "click" in script:
                self.events.append("tick")
            return None
        element_id = args[0]
        if "Button" in element_id:
            # null when absent, a state object when present.
            if not self._has(element_id):
                return None
            self.clicked.append(element_id)
            self.events.append("apply")
            return {"was_disabled": False}
        if not self._has(element_id):
            return False
        if len(args) > 1:
            # args: (id, display text, iso text, section)
            self.written[element_id] = args[1]
            self.written_iso[element_id] = args[2] if len(args) > 2 else None
            self.events.append(f"write:{element_id}")
        return True


def state(**overrides):
    """What the page reports after the postback — by default, exactly what was
    asked for."""
    base = {
        "mode_checked": True,
        "range_start": "08/04/2026",
        "range_end": "08/10/2026",
        "day": "",
        "within": "",
    }
    base.update(overrides)
    return base


def driver_for(**fields):
    fake = FakeDriver(**fields)
    notes: list[str] = []
    return fake, notes, SidebarDriver(fake, note=notes.append)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """No real waiting, but the wait is still traced — its *position* relative to
    the tick and the writes is the thing the panel gets wrong."""
    def traced(self):
        driver = self.driver
        if hasattr(driver, "events"):
            driver.events.append("postback")

    def traced_replaced(self, anchor):
        driver = self.driver
        if hasattr(driver, "anchors_taken"):
            driver.anchors_taken.append(anchor)
        traced(self)
        outcome = getattr(driver, "replaced", True)
        if isinstance(outcome, list):
            return outcome.pop(0) if outcome else True
        return outcome

    monkeypatch.setattr(sidebar_module.SidebarDriver, "_await_postback", traced)
    monkeypatch.setattr(sidebar_module.SidebarDriver, "_await_replaced", traced_replaced)


# -- the happy path -----------------------------------------------------------


def test_a_range_is_written_into_both_fields_and_applied():
    fake, notes, driver = driver_for(page_state=state())

    assert driver._apply_date("published_date", RANGE) is True
    assert fake.written["publishedDateRANGE1"] == "08/04/2026"
    assert fake.written["publishedDateRANGE2"] == "08/10/2026"
    assert "publishedDateSearchButton" in fake.clicked
    assert notes == []


# -- the silent no-ops --------------------------------------------------------


def test_a_missing_date_field_is_reported_instead_of_passing_as_applied():
    """The core defect: writing into an element that is not there returned
    nothing, so an empty range posted and the panel called it a success."""
    fake, notes, driver = driver_for(
        present={"publishedDateRANGE1", "publishedDateSearchButton"},   # RANGE2 gone
        page_state=state(),
    )

    assert driver._apply_date("published_date", RANGE) is False
    assert notes and "publishedDateRANGE2" in notes[0]
    assert "unreliable" in notes[0]


def test_a_missing_apply_button_means_the_dates_never_posted():
    fake, notes, driver = driver_for(
        present={"publishedDateRANGE1", "publishedDateRANGE2"},         # no button
        page_state=state(),
    )

    assert driver._apply_date("published_date", RANGE) is False
    assert notes and "no Apply button" in notes[0]
    assert "never submitted" in notes[0]
    assert "unfiltered by date" in notes[0], "say what that means for the results"


def test_a_panel_the_portal_does_not_offer_is_skipped_not_failed():
    _, notes, driver = driver_for(checkbox=False)

    assert driver._apply_date("published_date", RANGE) is False
    assert notes and "no 'RANGE' option" in notes[0]


# -- verification -------------------------------------------------------------


def test_dates_the_portal_did_not_keep_are_reported():
    """The portal reshaping or dropping the window must not be reported as the
    window that was asked for."""
    _, notes, driver = driver_for(page_state=state(range_start="01/01/2026"))

    assert driver._apply_date("published_date", RANGE) is False
    assert notes and "did not keep the date filter" in notes[0]
    assert "08/04/2026" in notes[0] and "01/01/2026" in notes[0]


def test_an_unticked_mode_checkbox_is_reported():
    _, notes, driver = driver_for(page_state=state(mode_checked=False))

    assert driver._apply_date("published_date", RANGE) is False
    assert notes and "not ticked" in notes[0]


def test_a_verified_window_reports_clean():
    _, notes, driver = driver_for(page_state=state())
    assert driver._apply_date("published_date", RANGE) is True
    assert notes == []


# -- what the run record says -------------------------------------------------


def test_the_summary_names_the_actual_window_not_just_the_mode():
    """`published_date=RANGE` next to an empty export explains nothing. The
    dates are the whole point."""
    summary = SidebarFilterRequest(status="OPEN", published_date=RANGE).summary()
    assert "published_date=RANGE 08/04/2026–08/10/2026" in summary


def test_every_date_mode_describes_itself():
    assert DateFilter(type="WITHIN", within="WEEK").describe() == "WITHIN WEEK"
    assert DateFilter(type="DAY", day="08/04/2026").describe() == "DAY 08/04/2026"
    assert DateFilter(type="SINCE_LAST_LOGIN").describe() == "SINCE_LAST_LOGIN"
    assert DateFilter(type="RANGE").describe() == "RANGE ?–?"


# -- what an empty result costs ----------------------------------------------


def test_an_empty_result_ends_the_postback_wait_instead_of_timing_out(monkeypatch):
    """Waiting only on rows made "no results" the slowest outcome: the wait
    could not be satisfied, so it burned the full 60s timeout on every keyword.
    The portal states emptiness on its group badges — that ends the wait."""
    import app.scrapers.bidnet.sidebar as sb

    seen = {"script": None}

    class EmptyPage:
        def execute_script(self, script, *args):
            seen["script"] = script
            # No rows anywhere, and every result group reports zero.
            return bool(
                "solicitationCount" in script and "querySelector(arguments[0])" in script
            )

    monkeypatch.setattr(sb.time, "sleep", lambda _s: None)
    # This one test needs the real wait, not the autouse stub above.
    monkeypatch.setattr(sb.SidebarDriver, "_await_postback", _REAL_AWAIT_POSTBACK)
    driver = sb.SidebarDriver(EmptyPage())
    driver._await_postback()      # must return promptly, not raise or hang

    assert "solicitationCount" in (seen["script"] or ""), "emptiness must be waited on"


# -- the write must land in the page the tick's postback returns ---------------


def test_the_ticks_postback_is_waited_out_before_the_dates_are_written():
    """The live failure: the tick, both writes and Apply all completed inside
    ~170ms, so the dates went into a DOM the tick's in-flight postback then
    replaced. What reached BidNet was RANGE with no dates — which it answers
    with zero results for every keyword, exactly what the run showed."""
    fake, _, driver = driver_for(ticked=False, page_state=state())

    assert driver._apply_date("published_date", RANGE) is True

    tick = fake.events.index("tick")
    first_write = next(i for i, e in enumerate(fake.events) if e.startswith("write:"))
    assert "postback" in fake.events[tick:first_write], (
        f"a postback must be waited out between the tick and the writes: {fake.events}"
    )


def test_an_already_ticked_mode_does_not_pay_for_a_postback():
    """Nothing was clicked, so there is no postback to wait for."""
    fake, _, driver = driver_for(ticked=True, page_state=state())

    assert driver._apply_date("published_date", RANGE) is True
    assert "tick" not in fake.events
    first_write = next(i for i, e in enumerate(fake.events) if e.startswith("write:"))
    assert "postback" not in fake.events[:first_write]


def test_writes_lost_to_a_postback_are_rewritten_once():
    """First read shows the fields blank — the writes were lost. The panel must
    rewrite them into the settled page rather than report a broken filter."""
    fake, notes, driver = driver_for(
        page_state=[state(range_start="", range_end=""), state()],   # blank, then good
    )

    assert driver._apply_date("published_date", RANGE) is True
    assert fake.events.count("write:publishedDateRANGE1") == 2, "rewritten after the miss"
    assert fake.clicked.count("publishedDateSearchButton") == 2
    assert notes == [], "a recovered attempt is not worth reporting"


def test_a_filter_that_never_sticks_is_reported_after_the_retry():
    fake, notes, driver = driver_for(
        page_state=[state(range_start="", range_end=""), state(range_start="", range_end="")],
    )

    assert driver._apply_date("published_date", RANGE) is False
    assert notes and "did not keep the date filter" in notes[0]
    assert "empty date range" in notes[0], "say what an empty range does to the results"
    assert "zero results for every keyword" in notes[0]


def test_the_hidden_twin_counts_as_the_value_being_kept():
    """The `_hidden` field is what the form posts; the visible one can re-render
    blank. Reading only the visible field reported a working filter as broken."""
    import app.scrapers.bidnet.sidebar as sb

    class TwinPage:
        """Visible fields blank, hidden twins carrying the dates."""

        values = {
            "publishedDateRANGE1_hidden": "08/04/2026",
            "publishedDateRANGE2_hidden": "08/10/2026",
        }

        def execute_script(self, script, *args):
            assert "_hidden" in script, "the twin must be read"
            return {
                "mode_checked": True,
                "range_start": self.values["publishedDateRANGE1_hidden"],
                "range_end": self.values["publishedDateRANGE2_hidden"],
                "day": "",
                "within": "",
            }

    notes: list[str] = []
    driver = sb.SidebarDriver(TwinPage(), note=notes.append)
    assert driver._verify_date("published_date", "publishedDate", RANGE) is True
    assert notes == []


# -- the two formats, from the panel's own markup -----------------------------
#
#   <input id="publishedDateRANGE1"        value="08/04/2026">
#   <input id="publishedDateRANGE1_hidden" value="2026-08-04"
#          name="publishedDate.localRangeStart">
#
# The twin is what posts, and it is ISO. Writing the US format into it posts a
# start date the server cannot read, so the range collapses to empty and every
# keyword returns zero while the panel still looks correctly filled in.


def test_the_posted_twin_gets_iso_and_the_visible_field_gets_us():
    fake, _, driver = driver_for(page_state=state())

    assert driver._apply_date("published_date", RANGE) is True
    assert fake.written["publishedDateRANGE1"] == "08/04/2026"
    assert fake.written_iso["publishedDateRANGE1"] == "2026-08-04"
    assert fake.written["publishedDateRANGE2"] == "08/10/2026"
    assert fake.written_iso["publishedDateRANGE2"] == "2026-08-10"


def test_iso_conversion_matches_the_panels_format():
    from app.scrapers.bidnet.sidebar import _iso

    assert _iso("08/04/2026") == "2026-08-04"
    assert _iso("8/4/2026") == "2026-08-04", "the panel accepts unpadded input"
    assert _iso("12/31/2026") == "2026-12-31"


def test_an_unparseable_date_yields_no_iso_rather_than_a_plausible_wrong_one():
    from app.scrapers.bidnet.sidebar import _iso

    for bad in ("", "2026-08-04", "08/04/26", "next tuesday", "08/04"):
        assert _iso(bad) == "", f"{bad!r} must not become a date"


def test_the_read_back_compares_dates_by_meaning_not_by_text():
    """The visible field reads 08/04/2026 and the twin reads 2026-08-04. A
    literal comparison would report a correctly applied window as drifted on
    every run — the previous fix's own read-back would have done exactly that."""
    _, notes, driver = driver_for(
        page_state=state(range_start="2026-08-04", range_end="2026-08-10"),
    )

    assert driver._apply_date("published_date", RANGE) is True
    assert notes == []


def test_a_genuinely_different_date_is_still_caught():
    """Format-tolerant must not mean value-blind."""
    _, notes, driver = driver_for(page_state=state(range_start="2026-01-01"))

    assert driver._apply_date("published_date", RANGE) is False
    assert notes and "did not keep the date filter" in notes[0]


# -- the panel's own validation ----------------------------------------------


def test_the_panels_validation_message_is_read_instead_of_guessed_at():
    """`<div class="message151 error hidden">Ending date must be greater or
    equal to the starting date.</div>` — unhidden when it fires. Reading it
    turns a filter the portal silently refused into a stated reason."""
    fake, notes, driver = driver_for(
        page_state=state(),
        complaint="Ending date must be greater or equal to the starting date.",
    )

    assert driver._apply_date("published_date", RANGE) is False
    assert notes and "BidNet rejected the dates" in notes[0]
    assert "greater or equal" in notes[0]
    assert fake.clicked == [], "a rejected panel is not applied"


def test_a_happy_panel_reports_no_complaint():
    fake, notes, driver = driver_for(page_state=state(), complaint="")
    assert driver._apply_date("published_date", RANGE) is True
    assert "publishedDateSearchButton" in fake.clicked


# -- waiting for the results to be REPLACED, not merely present ---------------


def test_the_apply_waits_on_a_results_anchor_going_stale():
    """The bug this exists for, measured on a live run: a Published Date window
    of 08/04–08/10 exported six solicitations published 06/15 to 08/03, all six
    from the run's *first* keyword.

    The panel had genuinely applied and verified the window. But the wait after
    Apply asked "is there a result row on the page?" — and the portal leaves the
    previous rows in the DOM until the postback swaps them in, so that is true
    the instant it is asked, on the pre-filter page. `collect_links` then read
    the unfiltered rows. Only watching a node from the current results go stale
    proves the swap happened."""
    fake, _, driver = driver_for(page_state=state())

    assert driver._apply_date("published_date", RANGE) is True
    assert fake.anchors_taken, "the wait must watch a node from the current results"
    assert fake.anchor in fake.anchors_taken, "and it must be a real results node"


def test_a_page_with_no_rows_falls_back_to_the_plain_wait():
    """A search that matched nothing has no row to go stale, which is a valid
    state — the panel must not hang waiting for one."""
    fake, notes, driver = driver_for(page_state=state(), rows=False)

    assert driver._apply_date("published_date", RANGE) is True
    assert fake.anchors_taken == [None]


def test_every_filter_postback_uses_the_replacement_wait():
    """Not just the date panel. Status, the bulk field submit and a single
    checkbox toggle all re-render the same results list, and all three used the
    presence wait that cannot distinguish the old page from the new one."""
    import inspect

    source = inspect.getsource(sidebar_module.SidebarDriver)
    body = source.split("def _await_replaced")[0]      # exclude the helper itself
    assert "_await_postback()" not in body, (
        "a filter postback is still using the presence wait; it must use "
        "_await_replaced(anchor)"
    )
    assert body.count("_await_replaced(anchor)") >= 5


# -- pressing Apply is not the same as applying -------------------------------


def test_an_apply_that_does_not_post_is_pressed_again():
    """The Apply button is a commandButton built with `enabled: "false"`, so it
    can ignore a click even after the CSS class and aria-disabled are cleared.
    When it does, the date fields still hold the values *we* wrote into the DOM,
    so reading them back proves nothing — the only proof is the results being
    replaced."""
    fake, notes, driver = driver_for(page_state=state(), replaced=[False, True])

    assert driver._apply_date("published_date", RANGE) is True
    assert fake.clicked.count("publishedDateSearchButton") == 2, "pressed again"
    assert notes == [], "a press that landed on the retry is not a problem"


def test_an_apply_that_never_posts_is_reported_as_unfiltered():
    """After every attempt, the run must say the results are not date-restricted
    rather than report the window as applied."""
    fake, notes, driver = driver_for(page_state=state(), replaced=False)

    assert driver._apply_date("published_date", RANGE) is False
    assert fake.clicked.count("publishedDateSearchButton") == SidebarDriver.APPLY_ATTEMPTS
    assert notes and "did not act on the date panel's Apply" in notes[0]
    assert "NOT restricted to the requested dates" in notes[0]


def test_a_verified_read_back_is_not_enough_on_its_own():
    """The heart of it: the page reports exactly the window that was asked for —
    because we wrote it there — yet the Apply never posted. That must not count
    as applied."""
    _, notes, driver = driver_for(page_state=state(), replaced=False)

    assert driver._apply_date("published_date", RANGE) is False, (
        "fields holding the right text is not evidence the portal filtered on it"
    )
