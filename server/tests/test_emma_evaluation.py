"""EMMA's bid screen — the blocklist, the master-contract skip, and RFIs.

The screen is a keyword blocklist rather than a decision matrix (see
`model evalution matrix for emma.md` in the repository root for the matrix that
is specified but not built). What it does do, it has to do exactly: a phrase
that fires one word too eagerly deletes a real bid from the run, and EMMA drops
rejected bids rather than marking them, so nothing downstream shows it happened.

That asymmetry is what most of these tests are about — "Audit" must not fire on
"auditorium", and "RFI" must not fire inside another word.

    server/.venv/bin/python -m pytest server/tests/test_emma_evaluation.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.scrapers.emma import evaluation  # noqa: E402


def screen(title="", **fields):
    return evaluation.evaluate({"title": title, **fields})


# =============================================================================
# Requests for Information — discarded, and why they are their own category
# =============================================================================


@pytest.mark.parametrize("title", [
    "Request for Information — Statewide Broadband Capabilities",
    "Requests for Information from Interested Vendors",
    "RFI: Cloud Hosting Capabilities",
    "Vendor RFI for Fleet Telematics",
])
def test_a_request_for_information_is_discarded(title):
    """An RFI is not a solicitation. It asks the market to describe what it can
    supply so the agency can write a real bid later — there is nothing to price
    and nothing to win."""
    verdict = screen(title)

    assert verdict["decision"] == "REJECT"
    assert verdict["matched_rule"] == "not_biddable"


def test_the_rfi_reason_is_recorded_apart_from_the_scope_blocklist():
    """Three lists mean three different things, and the run log says which
    fired: out-of-scope work, master-contract-only, and not biddable at all."""
    rfi = screen("Request for Information — Broadband")
    scope = screen("Building Renovation, Phase 2")
    master = screen("Only Master Contracts may respond")

    assert rfi["matched_rule"] == "not_biddable"
    assert scope["matched_rule"] == "keyword"
    assert master["matched_rule"] == "master_contract"


def test_the_solicitation_type_field_catches_an_rfi_that_the_title_does_not():
    """EMMA types its solicitations in a column of its own, and that is the more
    reliable place to read this than a title someone wrote freehand."""
    verdict = screen("Statewide Broadband Capabilities",
                     solicitation_type="Request for Information")

    assert verdict["decision"] == "REJECT"
    assert verdict["matched_in"] == "bid"


def test_an_rfi_named_only_in_a_document_is_still_caught():
    verdict = evaluation.evaluate(
        {"title": "Broadband Capabilities"},
        doc_text="This Request for Information is issued to survey the market.",
    )

    assert verdict["decision"] == "REJECT"
    assert verdict["matched_in"] == "documents"


@pytest.mark.parametrize("title", [
    "Request for Proposals — Laptops",
    "Request for Quotation — Office Chairs",
    "Information Technology Supply Contract",
    "Sheriff Notification System",
    "Traffic Information Signage Supply",
])
def test_a_real_solicitation_is_not_mistaken_for_an_rfi(title):
    """"Request for Proposals" and "Information Technology" both contain most of
    the phrase; neither is an RFI."""
    assert screen(title)["decision"] == "PASS"


@pytest.mark.parametrize("title", [
    "Sheriff Vehicle Supply",          # "rfi" inside no word here
    "Airfield Lighting Replacement",
    "Serfing Equipment",               # contains "rfi" as a substring
])
def test_the_rfi_abbreviation_does_not_fire_inside_another_word(title):
    """Word-boundary anchored, which is what makes a three-letter phrase safe to
    put on a blocklist at all."""
    assert screen(title)["decision"] == "PASS"


# =============================================================================
# The rest of the screen, unchanged by this addition
# =============================================================================


@pytest.mark.parametrize("title, phrase", [
    ("Building Renovation, Phase 2", "Renovation"),
    ("Annual Financial Audit Services", "Audit"),
    ("Pest Control for State Facilities", "Pest Control"),
    ("Janitorial Services, Baltimore", "Janitorial Services"),
])
def test_the_scope_blocklist_still_fires(title, phrase):
    verdict = screen(title)

    assert verdict["decision"] == "REJECT"
    assert verdict["matched_keyword"] == phrase


@pytest.mark.parametrize("title", [
    "Auditorium Seating Supply",        # not "Audit"
    "Constructive Feedback Platform",   # not "Construction"
])
def test_a_blocked_word_does_not_fire_inside_a_longer_one(title):
    assert screen(title)["decision"] == "PASS"


def test_a_clean_bid_passes():
    verdict = screen("Supply of 400 Laptops and Docking Stations")

    assert verdict["decision"] == "PASS"
    assert verdict["matched_keyword"] == ""
    assert verdict["matched_rule"] == ""


def test_every_phrase_in_every_list_compiles_and_matches_itself():
    """A phrase that cannot match its own text is a rule that never fires, and
    nothing else in the pipeline would ever say so."""
    lists = (
        evaluation.REJECT_KEYWORDS,
        evaluation.MASTER_CONTRACT_PHRASES,
        evaluation.NOT_BIDDABLE_PHRASES,
    )
    for phrases in lists:
        for phrase in phrases:
            assert evaluation.find_match(phrase) is not None, phrase
