"""EMMA's Publish Date range: the verdict, the request parsing, and the scrape.

Pure logic and a stubbed grid — no browser, no portal, no DB.

What these pin down:

* a bid is kept only when its Publish Date is provably inside the range — both
  ends inclusive, compared as days, an unreadable date discarded and counted;
* the range is applied before the 7-day closing rule, and that rule still works
  exactly as before, with and without a range;
* paging stops early only when the grid has proved itself newest-first and a
  whole page is already older than the range's start — never on an unsorted grid.

    server/.venv/bin/python -m pytest server/tests/test_emma_date_range.py
"""

import os
import sys
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import run_manager  # noqa: E402
from app.core.closing_filter import MIN_DAYS_UNTIL_CLOSE  # noqa: E402
from app.scrapers.emma import router as emma_router  # noqa: E402
from app.scrapers.emma.scraper import (  # noqa: E402
    EmmaScraper,
    describe_window,
    publish_window_verdict,
)
from app.scrapers.myflorida.dates import PostingDateRange  # noqa: E402

OCT_1 = date(2026, 10, 1)
OCT_21 = date(2026, 10, 21)
OCTOBER = PostingDateRange(OCT_1, OCT_21)

# A close date comfortably past the 7-day rule, whatever day the suite runs.
FAR = (date.today() + timedelta(days=60)).strftime("%m/%d/%Y")
# One that the 7-day rule must drop.
SOON = (date.today() + timedelta(days=MIN_DAYS_UNTIL_CLOSE - 2)).strftime("%m/%d/%Y")


@pytest.fixture(autouse=True)
def _no_db_persistence(monkeypatch):
    """Runs created here must not land in the real run_state table, where the
    console's History would list them."""
    monkeypatch.setattr(run_manager, "_persist", lambda run: None)


# -- the verdict --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("10/1/2026 12:00:00 AM", "in"),       # the From day itself
        ("10/21/2026 11:59:00 PM", "in"),      # the very end of the To day
        ("10/10/2026 2:30:00 PM", "in"),
        ("10/10/2026", "in"),                  # no time part
        ("9/30/2026 11:59:59 PM", "before"),   # one minute short of the range
        ("10/22/2026 12:00:00 AM", "after"),
        ("8/21/2017 8:00:00 AM", "before"),    # a live solicitation published years ago
        ("", "unreadable"),
        (None, "unreadable"),
        ("TBD", "unreadable"),
    ],
)
def test_the_verdict_is_inclusive_and_compares_days(raw, expected):
    assert publish_window_verdict(raw, OCTOBER) == expected


def test_an_open_ended_range_bounds_only_one_side():
    since = PostingDateRange(OCT_1, None)
    until = PostingDateRange(None, OCT_21)
    assert publish_window_verdict("1/1/2030", since) == "in"
    assert publish_window_verdict("9/1/2026", since) == "before"
    assert publish_window_verdict("1/1/2000", until) == "in"
    assert publish_window_verdict("11/1/2026", until) == "after"


def test_the_range_is_described_in_words():
    assert describe_window(OCTOBER) == "published 2026-10-01 to 2026-10-21"
    assert describe_window(PostingDateRange(OCT_1, None)) == "published on or after 2026-10-01"
    assert describe_window(PostingDateRange(None, OCT_21)) == "published on or before 2026-10-21"
    assert describe_window(PostingDateRange()) == ""


# -- the request --------------------------------------------------------------


def test_the_request_accepts_iso_and_portal_dates_and_blanks():
    assert emma_router.parse_publish_window("2026-10-01", "10/21/2026") == OCTOBER
    assert emma_router.parse_publish_window("", "") == PostingDateRange()
    assert not emma_router.parse_publish_window("  ", "").is_set


def test_an_unreadable_date_is_a_400_naming_the_field():
    with pytest.raises(HTTPException) as caught:
        emma_router.parse_publish_window("2026-13-40", "")
    assert caught.value.status_code == 400
    assert "From" in caught.value.detail


def test_an_inverted_range_is_a_400_not_a_silent_swap():
    with pytest.raises(HTTPException) as caught:
        emma_router.parse_publish_window("2026-10-21", "2026-10-01")
    assert caught.value.status_code == 400
    assert "before the From date" in caught.value.detail


# -- the scrape ---------------------------------------------------------------


def _scraper(tmp_path, window=None):
    run = run_manager.create_run("emma", tmp_path, {"search": "test"})
    return EmmaScraper(run["run_id"], date_range=window)


def _row(emma_id, published, close=FAR):
    return {
        "emma_id": emma_id,
        "bpm_code": f"BPM{emma_id}",
        "title": f"Bid {emma_id}",
        "publish_date": published,
        "close_date": close,
        "detail_url": None,
    }


def _grid(scraper, monkeypatch, pages):
    """Stand in for the portal's grid: `_read_rows` returns the current page and
    `_go_next_page` turns it, as the real pager does. Returns the list of pages
    actually read, so a test can see where paging stopped."""
    read: list[int] = []
    state = {"page": 0}

    def read_rows():
        read.append(state["page"] + 1)
        return [dict(r) for r in pages[state["page"]]]

    def go_next():
        if state["page"] + 1 >= len(pages):
            return False
        state["page"] += 1
        return True

    monkeypatch.setattr(scraper, "_read_rows", read_rows)
    monkeypatch.setattr(scraper, "_go_next_page", go_next)
    return read


def _kept(scraper):
    return [r["emma_id"] for r in scraper._records]


def test_without_a_range_every_bid_reaches_the_seven_day_rule(tmp_path, monkeypatch):
    scraper = _scraper(tmp_path)
    _grid(scraper, monkeypatch, [[
        _row("1", "8/21/2017 8:00:00 AM"),
        _row("2", "10/10/2026 9:00:00 AM"),
        _row("3", "", close=FAR),
        _row("4", "10/10/2026 9:00:00 AM", close=SOON),
    ]])
    scraper.scrape_all_pages()

    assert _kept(scraper) == ["1", "2", "3"]
    assert scraper._skipped_closing_soon == 1
    assert scraper._skipped_outside_range == 0
    assert scraper._skipped_no_publish_date == 0


def test_the_seven_day_rule_still_keeps_an_unreadable_close_date(tmp_path, monkeypatch):
    scraper = _scraper(tmp_path, OCTOBER)
    _grid(scraper, monkeypatch, [[
        _row("1", "10/10/2026 9:00:00 AM", close=None),
        _row("2", "10/11/2026 9:00:00 AM", close=SOON),
    ]])
    scraper.scrape_all_pages()

    assert _kept(scraper) == ["1"]
    assert scraper._kept_unreadable_close == 1
    assert scraper._skipped_closing_soon == 1


def test_only_bids_published_in_range_are_kept(tmp_path, monkeypatch):
    scraper = _scraper(tmp_path, OCTOBER)
    _grid(scraper, monkeypatch, [[
        _row("in-1", "10/1/2026 12:00:00 AM"),
        _row("in-2", "10/21/2026 11:59:00 PM"),
        _row("old", "9/30/2026 5:00:00 PM"),
        _row("new", "10/22/2026 8:00:00 AM"),
        _row("blank", ""),
        _row("in-soon", "10/15/2026 9:00:00 AM", close=SOON),
    ]])
    scraper.scrape_all_pages()

    assert _kept(scraper) == ["in-1", "in-2"]
    assert scraper._skipped_outside_range == 2
    assert scraper._skipped_no_publish_date == 1
    # Inside the range, the 7-day rule still drops a bid closing too soon. A bid
    # outside the range is never counted by it: the range is checked first.
    assert scraper._skipped_closing_soon == 1


def test_a_repeated_row_is_counted_once(tmp_path, monkeypatch):
    scraper = _scraper(tmp_path, OCTOBER)
    _grid(scraper, monkeypatch, [
        [_row("1", "10/10/2026"), _row("2", "9/1/2026")],
        [_row("2", "9/1/2026"), _row("3", "10/12/2026")],
    ])
    scraper.scrape_all_pages()

    assert _kept(scraper) == ["1", "3"]
    assert scraper._skipped_outside_range == 1


# -- stopping early -----------------------------------------------------------


NEWEST_FIRST = [
    [_row("a", "10/20/2026"), _row("b", "10/15/2026")],
    [_row("c", "10/5/2026"), _row("d", "9/28/2026")],   # crosses Oct 1 part-way
    [_row("e", "9/10/2026"), _row("f", "8/1/2026")],    # the first page wholly older
    [_row("g", "7/1/2026"), _row("h", "6/1/2026")],     # never needs reading
]


def test_a_newest_first_grid_stops_once_a_whole_page_is_older_than_the_range(tmp_path, monkeypatch):
    scraper = _scraper(tmp_path, OCTOBER)
    read = _grid(scraper, monkeypatch, NEWEST_FIRST)
    scraper.scrape_all_pages()

    assert read == [1, 2, 3]
    assert scraper._stopped_paging_early
    assert _kept(scraper) == ["a", "b", "c"]
    assert scraper._skipped_outside_range == 3  # d, e, f — the pages actually read


def test_a_page_that_only_crosses_the_start_does_not_stop_paging(tmp_path, monkeypatch):
    """The first page is in order and ends before Oct 1, but its newest row is in
    range — so the next page is still read, and its in-range bid is kept."""
    scraper = _scraper(tmp_path, OCTOBER)
    read = _grid(scraper, monkeypatch, [
        [_row("a", "10/20/2026"), _row("b", "9/28/2026")],
        [_row("c", "9/27/2026"), _row("d", "9/26/2026")],
    ])
    scraper.scrape_all_pages()

    assert read == [1, 2]
    assert _kept(scraper) == ["a"]


def test_an_unsorted_grid_is_read_to_the_end(tmp_path, monkeypatch):
    scraper = _scraper(tmp_path, OCTOBER)
    read = _grid(scraper, monkeypatch, [
        [_row("a", "10/20/2026"), _row("b", "9/28/2026")],   # looks newest-first...
        [_row("c", "10/18/2026"), _row("d", "9/1/2026")],    # ...until a newer date appears
        [_row("e", "10/3/2026"), _row("f", "8/1/2026")],     # an in-range bid an early stop would lose
    ])
    scraper.scrape_all_pages()

    assert read == [1, 2, 3]
    assert not scraper._stopped_paging_early
    assert _kept(scraper) == ["a", "c", "e"]


def test_no_early_stop_without_a_from_date(tmp_path, monkeypatch):
    scraper = _scraper(tmp_path, PostingDateRange(None, OCT_21))
    read = _grid(scraper, monkeypatch, NEWEST_FIRST)
    scraper.scrape_all_pages()

    assert read == [1, 2, 3, 4]
    assert not scraper._stopped_paging_early


def test_no_early_stop_without_a_range(tmp_path, monkeypatch):
    scraper = _scraper(tmp_path)
    read = _grid(scraper, monkeypatch, NEWEST_FIRST)
    scraper.scrape_all_pages()

    assert read == [1, 2, 3, 4]
    assert len(scraper._records) == 8


def test_the_range_is_named_in_matched_filters(tmp_path):
    scraper = _scraper(tmp_path, OCTOBER)
    assert scraper._filters_summary() == "published 2026-10-01 to 2026-10-21"
    # The range is ours to apply; the portal's filter bar is left alone.
    assert not scraper._has_filters()
    assert scraper._has_criteria()
