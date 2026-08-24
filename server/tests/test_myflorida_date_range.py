"""The posting-date window: one meaning across all three MyFlorida modes.

Keyword search, commodity-code search and the ad-status sweep are three ways of
driving the *same* portal search form, so a window has to mean the same thing
whichever button started the run. These tests hold that: the same parser, the
same rejections, the same record on the run.

They also hold the part that is deliberately **not** finished. Typing the window
into MyFlorida's own Posting Start/End Date fields is still outstanding, and on
this portal an unapplied filter is invisible — MFMP renders no "no results"
message and its results table exists before a search is submitted, so a search
that was never narrowed is indistinguishable from one that was. A run given a
window it cannot honour therefore has to say so out loud, and that is asserted
here rather than left to be noticed in an export.

    server/.venv/bin/python -m pytest server/tests/test_myflorida_date_range.py
"""

import os
import sys
from datetime import date

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main  # noqa: E402
from app.core import jobs, run_manager  # noqa: E402
from app.scrapers.myflorida import dates  # noqa: E402
from app.scrapers.myflorida.scraper import MFMPScraper  # noqa: E402
from app.scrapers.myflorida.sweep.scraper import SweepScraper  # noqa: E402


# -- the parser -------------------------------------------------------------


def test_an_iso_window_is_read_and_converted_for_the_portal():
    """ISO on the wire, mm/dd/yyyy at the portal. The ambiguous format stays
    inside the process that knows which portal it is talking to."""
    window = dates.parse("2026-08-01", "2026-08-31")

    assert (window.start, window.end) == (date(2026, 8, 1), date(2026, 8, 31))
    assert (window.portal_start, window.portal_end) == ("08/01/2026", "08/31/2026")


def test_the_portal_format_is_accepted_too():
    """A caller reaching for the portal's own format should not be tripped up."""
    assert dates.parse("08/01/2026", None).start == date(2026, 8, 1)


@pytest.mark.parametrize(
    "start,end",
    [("2026-08-01", None), (None, "2026-08-31")],
    ids=["open-ended", "open-started"],
)
def test_either_end_may_stand_alone(start, end):
    """"Everything since the first of the month" is a normal thing to ask for.
    Demanding a closing date would only invite someone to type today's and get a
    window that quietly stops being right tomorrow."""
    window = dates.parse(start, end)

    assert window.is_set
    assert (window.start is None) != (window.end is None)


def test_no_window_is_the_old_behaviour():
    window = dates.parse(None, "")

    assert not window.is_set
    assert window.describe() == "any posting date"
    assert window.isoformat() == (None, None)


def test_an_inverted_window_is_rejected_not_silently_swapped():
    """The portal would return nothing for it, and on a search with no "no
    results" message an empty grid is exactly what gets mistaken for "there are
    no bids"."""
    with pytest.raises(dates.DateRangeError) as caught:
        dates.parse("2026-08-31", "2026-08-01")

    assert "before the start date" in str(caught.value)


def test_an_unreadable_date_names_the_field_and_the_format():
    with pytest.raises(dates.DateRangeError) as caught:
        dates.parse("last tuesday", None)

    assert "Start date" in str(caught.value)
    assert "yyyy-mm-dd" in str(caught.value)


def test_the_window_describes_itself_for_a_run_summary():
    """This string sits next to an empty export, where "posted on or after
    2026-08-01" explains it and a bare date does not."""
    assert dates.parse("2026-08-01", "2026-08-31").describe() == (
        "posted 2026-08-01 to 2026-08-31"
    )
    assert dates.parse("2026-08-01", None).describe() == "posted on or after 2026-08-01"
    assert dates.parse(None, "2026-08-31").describe() == "posted on or before 2026-08-31"


# -- the endpoints ----------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    """A test client whose runs are registered but never actually executed."""
    monkeypatch.setattr(jobs, "submit", lambda run_id, fn, *a: None)
    with TestClient(main.app) as c:
        yield c


def _category(client) -> str:
    return client.get("/myflorida/categories").json()["categories"][0]["key"]


def test_the_niche_endpoint_records_the_window(client):
    body = client.post(
        "/myflorida/scrape",
        json={
            "category": _category(client),
            "mode": "codes",
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
        },
    )
    assert body.status_code == 200, body.text
    data = body.json()
    assert (data["start_date"], data["end_date"]) == ("2026-08-01", "2026-08-31")
    assert data["date_range_summary"] == "posted 2026-08-01 to 2026-08-31"

    run = run_manager.get_run(data["run_id"])
    assert run["start_date"] == "2026-08-01"
    assert run["date_range_summary"] == "posted 2026-08-01 to 2026-08-31"


def test_the_sweep_endpoint_records_the_same_window(client):
    body = client.post(
        "/myflorida/sweep/scrape",
        json={"ad_statuses": ["open"], "start_date": "2026-08-01", "end_date": "2026-08-31"},
    )
    assert body.status_code == 200, body.text
    data = body.json()
    assert data["date_range_summary"] == "posted 2026-08-01 to 2026-08-31"
    assert run_manager.get_run(data["run_id"])["end_date"] == "2026-08-31"


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/myflorida/scrape", {"mode": "codes"}),
        ("/myflorida/sweep/scrape", {"ad_statuses": ["open"]}),
    ],
    ids=["niche", "sweep"],
)
def test_a_bad_window_is_a_400_on_the_button(client, path, payload):
    """Rejected before the run exists — not after a browser has opened and
    someone has sat waiting to type a one-time password."""
    if path == "/myflorida/scrape":
        payload = {**payload, "category": _category(client)}
    response = client.post(
        path, json={**payload, "start_date": "2026-08-31", "end_date": "2026-08-01"}
    )

    assert response.status_code == 400
    assert "before the start date" in response.json()["detail"]


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/myflorida/scrape", {"mode": "codes"}),
        ("/myflorida/sweep/scrape", {"ad_statuses": ["open"]}),
    ],
    ids=["niche", "sweep"],
)
def test_omitting_the_window_still_works(client, path, payload):
    """Every caller written before this field existed must be unaffected."""
    if path == "/myflorida/scrape":
        payload = {**payload, "category": _category(client)}
    response = client.post(path, json=payload)

    assert response.status_code == 200, response.text
    assert response.json()["start_date"] is None


# -- what the scraper does with it ------------------------------------------


def _run_record(tmp_path, **extra):
    return run_manager.create_run("myflorida", tmp_path, {"category": "x", **extra})


def test_the_window_reaches_both_scrapers(tmp_path):
    """One window, three modes — and the sweep inherits it from the same parent
    the niche flow uses, so it cannot drift."""
    window = dates.parse("2026-08-01", "2026-08-31")

    niche = MFMPScraper(_run_record(tmp_path)["run_id"], [], date_range=window)
    sweep = SweepScraper(_run_record(tmp_path)["run_id"], ["open"], None, window)

    assert niche.date_range is window
    assert sweep.date_range is window


def test_a_scraper_with_no_window_defaults_to_every_posting_date(tmp_path):
    scraper = MFMPScraper(_run_record(tmp_path)["run_id"], [])

    assert scraper.date_range.is_set is False


def test_a_window_that_cannot_be_applied_is_reported_loudly(tmp_path, monkeypatch):
    """The honesty guard. While the portal injection is outstanding, a run given
    a window covers every posting date — and on a portal with no "no results"
    message, nobody could tell from the output alone."""
    monkeypatch.setattr(dates, "PORTAL_DATE_FILTER_READY", False)
    run = _run_record(tmp_path)
    scraper = MFMPScraper(run["run_id"], [], date_range=dates.parse("2026-08-01", None))

    scraper.report_date_window()

    stored = run_manager.get_run(run["run_id"])
    assert stored["date_filter_applied"] is False
    assert any("NOT applied" in w for w in stored["warnings"]), stored["warnings"]


def test_a_run_with_no_window_says_nothing_about_dates(tmp_path):
    """No warning, no flag — a run that asked for nothing has nothing to report,
    and a notice on every run would train people to ignore it."""
    run = _run_record(tmp_path)
    MFMPScraper(run["run_id"], []).report_date_window()

    stored = run_manager.get_run(run["run_id"])
    assert not stored.get("warnings")
    assert "date_filter_applied" not in stored


def test_the_warning_stops_once_the_portal_injection_lands(tmp_path, monkeypatch):
    """Flipping the flag is the last line of that work; nothing else changes
    with it, and this is what proves it."""
    monkeypatch.setattr(dates, "PORTAL_DATE_FILTER_READY", True)
    run = _run_record(tmp_path)
    scraper = MFMPScraper(run["run_id"], [], date_range=dates.parse("2026-08-01", None))

    scraper.report_date_window()

    stored = run_manager.get_run(run["run_id"])
    assert stored["date_filter_applied"] is True
    assert not stored.get("warnings")
