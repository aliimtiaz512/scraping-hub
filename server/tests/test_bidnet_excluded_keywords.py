"""BidNet's Excluded Keywords filter: parsing what the user typed, and driving
the sidebar's Keywords panel with it.

Pure logic and a stubbed driver — no browser, no portal.

    server/.venv/bin/python -m pytest server/tests/test_bidnet_excluded_keywords.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from selenium.common.exceptions import NoSuchElementException  # noqa: E402

from app.scrapers.bidnet import sidebar as sidebar_module  # noqa: E402
from app.scrapers.bidnet.filters import SidebarFilterRequest  # noqa: E402
from app.scrapers.bidnet.sidebar import SidebarDriver  # noqa: E402


def request(**fields) -> SidebarFilterRequest:
    return SidebarFilterRequest(**fields)


# -- what the user typed ------------------------------------------------------


def test_commas_semicolons_and_newlines_all_separate_terms():
    assert request(excluded_keywords="training, janitorial; catering\nuniforms"
                   ).excluded_keyword_list() == ["training", "janitorial", "catering", "uniforms"]


def test_a_space_does_not_split_a_term():
    """"fire alarm" is one exclusion, not two — splitting on spaces would drop
    every solicitation mentioning fire *or* alarm."""
    assert request(excluded_keywords="fire alarm inspection").excluded_keyword_list() == [
        "fire alarm inspection"
    ]


def test_blanks_and_duplicates_are_dropped_and_order_kept():
    assert request(excluded_keywords="  training , , training,\n\njanitorial ,"
                   ).excluded_keyword_list() == ["training", "janitorial"]


def test_a_quoted_phrase_keeps_its_quotes():
    """Quoting is the portal's own phrase syntax — pass it through untouched."""
    assert request(excluded_keywords='"fire alarm", training').excluded_keyword_list() == [
        '"fire alarm"', "training",
    ]


# -- the portal's own syntax --------------------------------------------------


def test_terms_are_joined_with_OR_because_the_box_is_a_query_not_a_list():
    """Measured on the live portal: of 1371 results, `software` excluded 79 and
    `training` 55, but `software training` / `software, training` /
    `software\ntraining` all excluded 2 — read as one phrase. Only
    `software OR training` excluded both (120). Anything else silently filters
    almost nothing while looking like it worked."""
    assert request(excluded_keywords="software, training").excluded_keywords_expression() == (
        "software OR training"
    )


def test_a_multi_word_term_is_quoted_so_the_phrase_boundary_is_explicit():
    assert request(excluded_keywords="fire alarm, training").excluded_keywords_expression() == (
        '"fire alarm" OR training'
    )


def test_a_term_the_user_already_quoted_is_not_double_quoted():
    assert request(excluded_keywords='"fire alarm"').excluded_keywords_expression() == '"fire alarm"'


def test_one_term_needs_no_operator():
    assert request(excluded_keywords="training").excluded_keywords_expression() == "training"


def test_no_terms_is_an_empty_expression():
    assert request().excluded_keywords_expression() == ""
    assert request(excluded_keywords="  ,; ").excluded_keywords_expression() == ""


def test_no_keywords_means_nothing_to_apply():
    for value in ("", "   ", ",,\n ;"):
        assert request(excluded_keywords=value).excluded_keyword_list() == []
    assert request().excluded_keyword_list() == []


def test_the_run_summary_counts_the_exclusions():
    assert "excluded_keywords=2" in request(excluded_keywords="a, b").summary()
    assert "excluded_keywords" not in request().summary()


# -- driving the panel --------------------------------------------------------


class FakeDriver:
    """Records the script calls the sidebar makes, and answers them."""

    def __init__(self, textarea_present: bool = True):
        self.textarea_present = textarea_present
        self.scripts: list[tuple[str, tuple]] = []
        self.typed: str | None = None
        self.clicked: list[str] = []

    def execute_script(self, script, *args):
        self.scripts.append((script, args))
        if "excludedKeywords" in str(args[:1]) or (args and args[0] == "excludedKeywords"):
            if not self.textarea_present:
                return False
            self.typed = args[1]
            return True
        if args and isinstance(args[0], str) and "Button" in args[0]:
            self.clicked.append(args[0])
        return None

    # The driver looks for a results row to watch for replacement. Report none —
    # a filter that matches nothing has no rows, which is a valid state — and
    # raise what Selenium actually raises for it.
    def find_element(self, *_args, **_kwargs):
        raise NoSuchElementException("no rows")


def driver_for(**fields):
    fake = FakeDriver(**fields)
    notes: list[str] = []
    return fake, notes, SidebarDriver(fake, note=notes.append)


def test_the_expression_is_written_into_the_box_and_applied(monkeypatch):
    monkeypatch.setattr(sidebar_module.SidebarDriver, "_await_postback", lambda self: None)
    fake, notes, driver = driver_for()

    assert driver._apply_excluded_keywords('training OR "fire alarm"') is True
    assert fake.typed == 'training OR "fire alarm"'  
    assert sidebar_module.KEYWORDS_APPLY_ID in fake.clicked
    assert notes == []


def test_a_missing_panel_is_reported_and_the_run_carries_on(monkeypatch):
    """Same rule as every other panel: a partial filter beats no results."""
    monkeypatch.setattr(sidebar_module.SidebarDriver, "_await_postback", lambda self: None)
    fake, notes, driver = driver_for(textarea_present=False)

    assert driver._apply_excluded_keywords("training") is False
    assert fake.clicked == []                      # nothing applied, nothing clicked
    assert notes and "Excluded Keywords" in notes[0]


def test_apply_skips_the_panel_entirely_when_no_terms_were_given(monkeypatch):
    """The fallback rule: no keywords means the standard open search."""
    monkeypatch.setattr(sidebar_module.SidebarDriver, "_apply_status", lambda self, status: None)
    monkeypatch.setattr(sidebar_module.SidebarDriver, "_await_postback", lambda self: None)
    fake, _, driver = driver_for()

    report = driver.apply(request())
    assert "excluded_keywords" not in report
    assert fake.typed is None
    assert fake.clicked == []


def test_apply_reports_the_terms_it_applied(monkeypatch):
    monkeypatch.setattr(sidebar_module.SidebarDriver, "_apply_status", lambda self, status: None)
    monkeypatch.setattr(sidebar_module.SidebarDriver, "_await_postback", lambda self: None)
    fake, _, driver = driver_for()

    report = driver.apply(request(excluded_keywords="training, fire alarm"))
    assert report["excluded_keywords"] == ["training", "fire alarm"]
    assert report["excluded_expression"] == 'training OR "fire alarm"'
    assert fake.typed == 'training OR "fire alarm"' 
