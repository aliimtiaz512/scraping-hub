"""SAM.gov NAICS isolation: only the codes the user entered reach the sheet.

The reported symptom: twenty NAICS codes entered in the console, and the Excel
came back carrying bids for codes that were not among them. Two causes, both
covered here — no browser and no portal, because what these pin down is the
decision rather than the DOM.

  1. SAM matches an opportunity on a *secondary* NAICS while the detail page
     states only the primary, so a bid asked for under 541511 arrives reading
     238210. Nothing compared the extracted code against the entered list, so it
     went into the downloads, the database and the spreadsheet.
  2. `apply_naics_filter` clicked the first autocomplete row whatever it said,
     then logged the code it had *typed* as the one it selected — so a parent
     category in position 0 filtered the portal on a code nobody entered, and
     the log claimed success.

    server/.venv/bin/python -m pytest server/tests/test_sam_naics_guardrail.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.scrapers.sam.engine import navigation  # noqa: E402


def _scraper(entered_codes):
    """A scraper with the entered codes, without a browser or a config file."""
    from app.scrapers.sam.engine.sam_scraper import SAMGovScraper

    obj = object.__new__(SAMGovScraper)
    obj.naics_codes = [str(c).strip() for c in entered_codes if str(c).strip()]
    obj.naics_dropped = 0
    return obj


# =============================================================================
# The guardrail: what reaches the spreadsheet
# =============================================================================


@pytest.mark.parametrize("extracted,kept", [
    ("541511", True),
    ("518210", True),
    (" 541611 ", True),      # the detail page's own whitespace
    ("238210", False),       # Electrical Contractors — the reported symptom
    ("236220", False),
    ("5415", False),         # the parent category
    ("54151", False),        # a prefix of an entered code
    ("5415110", False),      # an entered code with a digit appended
])
def test_only_an_entered_code_reaches_the_sheet(extracted, kept):
    entered = ["541511", "518210", "541611"]

    assert _scraper(entered)._naics_allowed(extracted) is kept


def test_a_bid_whose_naics_could_not_be_read_is_dropped():
    """"We could not tell" is not "it matches". Letting these through would
    leave the hole open for exactly the records whose provenance is least
    clear."""
    assert _scraper(["541511"])._naics_allowed("") is False
    assert _scraper(["541511"])._naics_allowed(None) is False


def test_a_run_with_no_codes_entered_keeps_everything():
    """The guardrail narrows a search; it never invents one. A run started
    without NAICS codes must behave exactly as it did before."""
    unfiltered = _scraper([])

    assert unfiltered._naics_allowed("238210") is True
    assert unfiltered._naics_allowed("") is True


def test_the_entered_codes_are_normalised_once():
    """A config carrying whitespace or a stray blank must not make a bid look
    like it came from a code nobody entered."""
    scraper = _scraper([" 541511 ", "", "541512", "   "])

    assert scraper.naics_codes == ["541511", "541512"]
    assert scraper._naics_allowed("541511") is True


def test_twenty_entered_codes_admit_only_their_own():
    """The reported case, at its real size."""
    entered = [
        "541511", "541512", "541513", "541519", "541611", "541618", "541690",
        "541715", "541990", "518210", "541330", "541380", "541420", "541430",
        "541490", "541612", "541613", "541614", "541620", "541370",
    ]
    scraper = _scraper(entered)

    assert all(scraper._naics_allowed(code) for code in entered)
    for outsider in ("238210", "236220", "561730", "332710", "423430"):
        assert not scraper._naics_allowed(outsider), f"{outsider} reached the sheet"


# =============================================================================
# The autocomplete: the portal is filtered on the code that was entered
# =============================================================================


@pytest.mark.parametrize("text,matches", [
    ("541511 - Custom Computer Programming Services", True),
    ("  541511 - Custom Computer Programming Services", True),
    ("541511", True),
    # The rows that used to be clickable from position 0.
    ("5415 - Computer Systems Design and Related Services", False),
    ("541512 - Computer Systems Design Services", False),
    ("5415110 - a longer code", False),
    ("Custom Computer Programming Services (541511)", False),
    ("", False),
])
def test_a_dropdown_row_belongs_to_a_code_only_when_it_starts_with_it(text, matches):
    assert navigation._option_matches(text, "541511") is matches


def test_the_parent_category_is_not_mistaken_for_the_code():
    """The mis-click: typing 541511 and having SAM offer 5415 first meant the
    portal filtered on the parent — every child code's bids came back, and the
    log said 541511 had been selected."""
    assert not navigation._option_matches(
        "5415 - Computer Systems Design and Related Services", "541511")


# =============================================================================
# Construction: the scraper has to exist before any of the above matters
# =============================================================================


@pytest.fixture
def no_browser(monkeypatch):
    """Construct the scraper without launching Chrome."""
    from app.scrapers.sam.engine.sam_scraper import SAMGovScraper

    monkeypatch.setattr(SAMGovScraper, "setup_driver", lambda self: None)
    return SAMGovScraper


def test_the_scraper_can_be_constructed_the_way_the_runner_constructs_it(no_browser):
    """`__init__` took a `run_id` argument, never stored it, and `_load_config`
    read it by bare name — where it does not exist. Every construction raised
    `NameError: name 'run_id' is not defined`, so no SAM run could start at all;
    the failure was at the first line of the run, before the browser or the
    portal were involved."""
    scraper = no_browser(
        headless=True,
        date_filter="2026-08-01",
        date_to="2026-08-17",
        naics_codes=["622110", "561790", "541922"],
        award_notice=False,
        run_id="43d9dcc9e9b5",
    )

    assert scraper.run_id == "43d9dcc9e9b5"
    assert scraper.naics_codes == ["622110", "561790", "541922"]


def test_the_temp_docs_folder_is_named_for_the_run(no_browser):
    """Two concurrent SAM runs each delete a notice's folder once they have read
    the text out of it, so they need roots of their own."""
    scraper = no_browser(run_id="43d9dcc9e9b5")

    assert scraper._temp_docs_dir.name == "43d9dcc9e9b5"


def test_a_standalone_run_without_a_run_id_still_gets_its_own_folder(no_browser):
    """The CLI path passes no run_id; the object's id is equally unique
    in-process."""
    scraper = no_browser()

    assert scraper.run_id is None
    assert scraper._temp_docs_dir.name.startswith("local-")
