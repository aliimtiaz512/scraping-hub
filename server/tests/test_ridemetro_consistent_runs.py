"""RideMetro runs that gave a different count every time.

Three outcomes were being reported as if they were all results:

* 0 bids, "completed" — My Network was read before its cards had rendered,
  and an empty roster was accepted as the account's real network;
* a partial count — Chrome was OOM-killed part way through a long sweep, at a
  different agency each run;
* the full count — the run that happened to finish.

These pin the fixes: an empty roster fails the run, a roster with no Active
count is re-read until it stops growing, the browser is restarted every
RECYCLE_EVERY agencies, and a dead browser is restarted and the sweep carries
on. No browser, no portal, no DB.

    server/.venv/bin/python -m pytest server/tests/test_ridemetro_consistent_runs.py
"""

import os
import sys
from pathlib import Path

import pytest
from selenium.common.exceptions import InvalidSessionIdException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import run_manager  # noqa: E402
from app.scrapers.ridemetro import network, scraper as rm  # noqa: E402
from app.scrapers.ridemetro.network import Agency  # noqa: E402


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """The roster's re-read pauses are real seconds; tests don't wait for them."""
    monkeypatch.setattr(rm.time, "sleep", lambda s: None)


def _scraper():
    run = run_manager.create_run("ridemetro", Path("/tmp"), {"account": "fedpints"})
    return rm.RideMetroScraper(run["run_id"])


def _agencies(n, status="Complete"):
    return [Agency(name=f"Agency {i}", status=status, url=f"https://a{i}.example", agency_id=f"{i}-us")
            for i in range(1, n + 1)]


def _roster(monkeypatch, reads, expected=None):
    """Successive read_agencies results, and the Active tab's count."""
    reads = iter(reads)
    monkeypatch.setattr(network, "active_count", lambda driver: expected)
    monkeypatch.setattr(network, "read_agencies", lambda driver: next(reads))


# -- the roster -----------------------------------------------------------------


def test_an_empty_roster_fails_the_run_instead_of_reporting_zero_bids(monkeypatch):
    s = _scraper()
    _roster(monkeypatch, [[]] * rm.ROSTER_READ_ATTEMPTS)
    with pytest.raises(RuntimeError, match="My Network showed no agencies"):
        s.collect_agencies()


def test_an_account_whose_active_tab_says_zero_is_a_real_zero(monkeypatch):
    s = _scraper()
    _roster(monkeypatch, [[]], expected=0)
    s.collect_agencies()
    assert s._agencies == []


def test_with_no_active_count_the_roster_is_re_read_until_it_stops_growing(monkeypatch):
    s = _scraper()
    _roster(monkeypatch, [_agencies(3), _agencies(6), _agencies(6)])
    s.collect_agencies()
    assert len(s._agencies) == 6


def test_with_an_active_count_a_short_read_is_re_read_until_it_matches(monkeypatch):
    s = _scraper()
    _roster(monkeypatch, [_agencies(10), _agencies(24)], expected=24)
    s.collect_agencies()
    assert len(s._agencies) == 24
    assert not run_manager.get_run(s.run_id).get("warnings")


# -- the sweep ------------------------------------------------------------------


def _sweep(monkeypatch, n, die_at=None, restart_ok=True):
    """Run scrape_network over n Complete agencies with the browser stubbed.

    Each agency yields one bid. `die_at` makes that agency's first attempt hit a
    dead browser. Returns the scraper and how many restarts it asked for.
    """
    s = _scraper()
    s._agencies = _agencies(n)
    restarts = []
    died = set()

    def scrape(agency):
        if agency.name == f"Agency {die_at}" and agency.name not in died:
            died.add(agency.name)
            raise InvalidSessionIdException("invalid session id: browser has closed the connection")
        s._records.append({"ref_number": agency.name})
        agency.opportunities = 1

    def restart():
        restarts.append(len(s._records))
        return restart_ok

    monkeypatch.setattr(s, "_scrape_agency", scrape)
    monkeypatch.setattr(s, "_restart_browser", restart)
    monkeypatch.setattr(s, "_recover_to_network", lambda: None)
    monkeypatch.setattr(s, "screenshot", lambda name: None)
    s.scrape_network()
    return s, restarts


R = rm.RECYCLE_EVERY
LONG = 2 * R + 10          # long enough for two scheduled restarts


def test_a_long_sweep_restarts_the_browser_every_recycle_every_agencies(monkeypatch):
    s, restarts = _sweep(monkeypatch, LONG)
    assert restarts == [R, 2 * R]                  # before agencies R+1 and 2R+1
    assert len(s._records) == LONG                 # nothing lost across the restarts
    assert run_manager.get_run(s.run_id)["agencies_scraped"] == LONG


def test_a_dead_browser_is_restarted_and_the_sweep_carries_on(monkeypatch):
    s, restarts = _sweep(monkeypatch, LONG, die_at=R + 5)
    assert len(restarts) == 3                      # R+1, the crash at R+5, 2R+1
    assert len(s._records) == LONG                 # the dead agency retried, the rest swept
    assert [r["ref_number"] for r in s._records].count(f"Agency {R + 5}") == 1
    assert not run_manager.get_run(s.run_id)["errors"]


def test_if_the_browser_cannot_be_restarted_the_bids_so_far_are_kept(monkeypatch):
    s, restarts = _sweep(monkeypatch, 10, die_at=4, restart_ok=False)
    assert len(s._records) == 3                    # agencies 1-3; nothing duplicated
    errors = run_manager.get_run(s.run_id)["errors"]
    assert len(errors) == 1 and "after 3 of 10 agencies" in errors[0]


def test_a_short_sweep_never_restarts_the_browser(monkeypatch):
    s, restarts = _sweep(monkeypatch, rm.RECYCLE_EVERY)
    assert restarts == []
    assert len(s._records) == rm.RECYCLE_EVERY


# -- saving ---------------------------------------------------------------------


def test_a_long_reference_number_fits_the_column():
    """One agency typed a 141-character project title into "Ref. #"; at
    VARCHAR(128) that one value rolled back the whole run's save."""
    from sqlalchemy import Text

    from app.scrapers.ridemetro.models import RideMetroBid

    assert isinstance(RideMetroBid.__table__.c.ref_number.type, Text)
    assert RideMetroBid.__table__.c.ref_number.type.length is None
