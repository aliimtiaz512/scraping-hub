"""What breaks on a run that lasts hours rather than minutes.

Both failures here came off one real member agency sweep: 1,859 bids collected
correctly, then 538 of them lost, then the surviving 1,321 lost too. Neither had
anything to do with scraping — a niche run never lasts long enough to meet
either.

* **The session expires mid-run, silently.** BidNet does not error when a
  session lapses; it serves a signed-out page, which has none of the
  solicitation's fields on it, so the bid reads as EXTRACTION_FAILED. The run
  read 1,321 bids correctly and then failed every remaining one in a row.
* **One oversized field aborts the whole save.** Postgres does not truncate an
  over-long value, it raises — and the run saves in a single transaction, so one
  agency writing prose into a date field cost every other bid in the run.

    server/.venv/bin/python -m pytest server/tests/test_bidnet_long_run.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import run_manager  # noqa: E402
from app.scrapers.bidnet.export import _COLUMN_LIMITS, _fit  # noqa: E402
from app.scrapers.bidnet.models import BidnetBid  # noqa: E402
from app.scrapers.bidnet.scraper import BidnetScraper  # noqa: E402

FIELDS = {
    "reference_number": "0000431640",
    "solicitation_number": "2026-008",
    "solicitation_type": "RFP",
    "title": "Destination Dutchess Strategic Planning Consultant",
    "publication_date": "08/01/2026",
    "question_acceptance_deadline": "09/01/2026",
    "closing_date": "09/18/2026 05:00 PM EDT",
}
EMPTY = {key: "" for key in FIELDS}


@pytest.fixture
def scraper(monkeypatch, tmp_path):
    run = run_manager.create_run("bidnet", tmp_path, {"niche_label": "X"})
    instance = BidnetScraper(run["run_id"], ["kw"], None, "X")
    monkeypatch.setattr(instance, "_acknowledgement_gate", lambda: None)
    return instance


# -- the session that lapses halfway through --------------------------------


def test_a_page_that_reads_empty_checks_the_session_before_retrying(scraper, monkeypatch):
    """The fix for the 538. Reloading alone can never recover a lapsed session —
    the reload fetches the same signed-out page."""
    calls = {"logged_in": 0, "scraped": 0}

    def scrape(link):
        calls["scraped"] += 1
        # Empty until the session is restored, exactly as the portal behaves.
        return dict(FIELDS) if calls["logged_in"] else dict(EMPTY)

    monkeypatch.setattr(scraper, "_scrape_detail", scrape)
    monkeypatch.setattr(
        scraper, "ensure_logged_in", lambda: calls.__setitem__("logged_in", calls["logged_in"] + 1)
    )

    record = scraper.process_bid("https://b/1")

    assert calls["logged_in"] == 1, "the session was never checked"
    assert record["status"] == "OK", record
    assert record["reference_number"] == FIELDS["reference_number"]


def test_a_healthy_read_never_touches_the_session(scraper, monkeypatch):
    """The check is paid only by a bid that already failed to read — otherwise it
    would be one extra round trip per bid across a couple of thousand of them."""
    checked = []
    monkeypatch.setattr(scraper, "_scrape_detail", lambda link: dict(FIELDS))
    monkeypatch.setattr(scraper, "ensure_logged_in", lambda: checked.append(1))

    scraper.process_bid("https://b/1")

    assert checked == []


def test_a_genuinely_empty_bid_is_still_flagged(scraper, monkeypatch):
    """Signing in again must not turn "this bid has no data" into a hang or a
    silent pass — it is still reported, with its URL, for a human to chase."""
    monkeypatch.setattr(scraper, "_scrape_detail", lambda link: dict(EMPTY))
    monkeypatch.setattr(scraper, "ensure_logged_in", lambda: None)

    record = scraper.process_bid("https://b/1")

    assert record["status"] == "EXTRACTION_FAILED"
    assert record["detail_url"] == "https://b/1"


def test_the_sweep_checks_the_session_periodically():
    """Recovery is the safety net; keeping the session alive is the cheaper
    half. A sweep runs well past BidNet's ~1 hour expiry."""
    from app.scrapers.bidnet import member_agencies

    assert 0 < member_agencies.SESSION_CHECK_EVERY <= 500


# -- the one field that sank a whole run ------------------------------------


def test_an_oversized_value_is_trimmed_rather_than_left_to_raise():
    """`solicitation_type` is still bounded (128) — the three date fields are
    Text now, so they are correctly absent from the limits map."""
    values = {"run_id": "abc", "solicitation_type": "Request for Proposals — " * 20}
    _fit(values)

    assert len(values["solicitation_type"]) <= _COLUMN_LIMITS["solicitation_type"]
    assert values["solicitation_type"].endswith("…"), "a trim should be visible"


def test_the_agency_name_cannot_overflow_its_column():
    """The column that actually failed first, and the one a sweep stresses: a
    member agency's own name, in the `niche` column."""
    values = {"run_id": "abc", "niche": "County of " + "Extremely Long " * 40}
    _fit(values)

    assert len(values["niche"]) <= _COLUMN_LIMITS["niche"]


def test_every_length_limited_column_is_covered():
    """Read off the model rather than listed here, so a column added later
    cannot quietly fall outside the guard."""
    limited = {
        c.name for c in BidnetBid.__table__.columns if getattr(c.type, "length", None)
    }
    assert limited == set(_COLUMN_LIMITS), limited ^ set(_COLUMN_LIMITS)


def test_normal_values_pass_through_untouched():
    values = {"run_id": "abc", **FIELDS}
    original = dict(values)
    _fit(values)

    assert values == original


def test_the_date_fields_are_unbounded_text():
    """They are not dates. They are whatever the agency typed, and a sweep across
    five hundred agencies always finds one nobody predicted."""
    for name in ("publication_date", "question_acceptance_deadline", "closing_date"):
        column = BidnetBid.__table__.c[name]
        assert getattr(column.type, "length", None) is None, f"{name} is still bounded"
