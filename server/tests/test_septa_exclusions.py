"""SEPTA summary blacklist and the optional Open Date Range.

Both are pure logic — no browser, no portal, no DB.

The summaries below are **real rows** scraped from SEPTA's Open Quotes grid
(pulled out of the archived exports in data/archives). They are the point of
this file: the blacklist has to drop OEM parts without taking legitimate quotes
with them, and two of the four terms are short enough that a plain substring
match quietly does exactly that.

    server/.venv/bin/python server/tests/test_septa_exclusions.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.scrapers.septa.exclusions import (  # noqa: E402
    EXCLUDED_SUMMARY_TERMS,
    excluded_by,
    is_excluded,
)
from app.scrapers.septa.filters import BadDate, OpenDateFilter  # noqa: E402

# -- real scraped summaries that MUST be excluded ---------------------------
# GASKET and CUMMINS are independent terms, so a summary naming either goes —
# adjacency is not required and was the original bug.
REAL_EXCLUDED = [
    ("GASKET CUMMINS 3974127 FILTER HEAD", "GASKET"),
    ("HEAD CUMMINS 3955034 LUBE OIL FILTER", "CUMMINS"),
    ("HOSE CUMMINS 5267681 FLEXIBLE APPROVED", "CUMMINS"),
    ("INJECTOR CUMMINS 5289380 FUEL APPROVED", "CUMMINS"),
    ("MODULE CUMMINS 5579356RX PARTICULATE", "CUMMINS"),
    ("SCREW CUMMINS 3903112 HEX HEAD CAP", "CUMMINS"),
    # both words present but not adjacent — the phrase rule let this through.
    # Reported as GASKET because that term comes first in the list.
    ("KIT CUMMINS 3977913 GASKET, LUBE OIL", "GASKET"),
    # a gasket from a different manufacturer still goes
    ("GASKET MEULLER INDUSTRIES P35708", "GASKET"),
    ("FILTER NF 6321254 AIR", "NF"),
    ("INSERT NF 051282 ALUMINUM .25 IN X 20", "NF"),
    ("LATCH NF 8110774 RH", "NF"),
    ("LATCH NF 8110775 LH", "NF"),
    ("TRIM NF 286168 DOOR AFT EXIT DOOR TRIM", "NF"),
    # parenthesised — '(' is not a word character, so the boundary still holds
    ("RIVET USSC 9904-000018-005 (NF 6406513)", "NF"),
    ("BRACKET NEW FLYER 695953 ASSY-ORBSTAR", "NEW FLYER"),
    ("CABLE NEW FLYER PARTS 470081 BATTERY,", "NEW FLYER"),
    ("SWITCH NEW FLYER 843408 LOW COOLANT", "NEW FLYER"),
]

# -- real scraped summaries that MUST survive -------------------------------
REAL_KEPT = [
    # The regression this module exists for: INFI(NF)INEON.
    "IGBT INFINEON BSM400GA170DLC NO",
    "STEP SEPTA SPEC S-3544-9 DWG D-4948-F",
    "HOLOPHANE - MGLEDM P15 40K MVOLT ZT FT",
]

# -- substring traps: words that CONTAIN a term but are not it --------------
SUBSTRING_TRAPS = [
    "CONFIGURATION MODULE 12345",     # CO(NF)IGURATION
    "TRANFER CASE ASSEMBLY",          # TRA(NF)ER
    "MANFOLD BRACKET 998877",         # MA(NF)OLD — no real term in this string
    "INNOVATION PARTNERS LLC 4471",   # IN(NOVA)TION
    "INNOVATIVE LIGHTING 88231",      # IN(NOVA)TIVE
    "INFO PANEL REPLACEMENT",         # I(NF)O
]


# -- exclusions -------------------------------------------------------------


def test_every_real_blacklisted_summary_is_excluded():
    for summary, expected in REAL_EXCLUDED:
        got = excluded_by(summary)
        assert got == expected, f"{summary!r} -> {got!r}, expected {expected!r}"


def test_real_legitimate_summaries_survive():
    for summary in REAL_KEPT:
        assert excluded_by(summary) is None, f"{summary!r} was wrongly excluded"


def test_infineon_is_not_mistaken_for_nf():
    """The measured false positive: substring matching drops this real quote."""
    summary = "IGBT INFINEON BSM400GA170DLC NO"
    assert "nf" in summary.lower(), "test premise: the substring really is present"
    assert excluded_by(summary) is None


def test_substring_traps_all_survive():
    for summary in SUBSTRING_TRAPS:
        assert excluded_by(summary) is None, f"{summary!r} was wrongly excluded"


def test_matching_is_case_insensitive():
    for summary in ("latch nf 8110774 rh", "Bracket New Flyer 695953", "gasket cummins 3974127"):
        assert is_excluded(summary), summary


def test_phrases_tolerate_extra_whitespace():
    """Only NEW FLYER is a phrase now — the manufacturer's name is two words."""
    assert excluded_by("BRACKET NEW  FLYER 695953") == "NEW FLYER"
    assert excluded_by("BRACKET NEW\tFLYER 695953") == "NEW FLYER"


def test_a_term_at_the_very_start_or_end_still_matches():
    assert excluded_by("NF 6321254 AIR FILTER") == "NF"
    assert excluded_by("AIR FILTER 6321254 NF") == "NF"


def test_nova_matches_as_a_word_only():
    assert excluded_by("SEAT NOVA 4471 ASSY") == "NOVA"
    assert excluded_by("NOVABUS PANEL 4471") is None, "NOVABUS is not the word NOVA"


def test_empty_and_missing_summaries_are_kept():
    """A blank summary is a parsing problem, not grounds to drop the row."""
    for value in (None, "", "   "):
        assert excluded_by(value) is None
        assert is_excluded(value) is False


def test_the_blacklist_is_exactly_what_was_specified():
    assert EXCLUDED_SUMMARY_TERMS == ("GASKET", "CUMMINS", "NF", "NOVA", "NEW FLYER")


def test_gasket_and_cummins_are_independent_not_a_phrase():
    """The reported bug: as a phrase, only adjacent words matched.

    Seven Cummins parts came through untouched because their summaries did not
    happen to put GASKET immediately before CUMMINS.
    """
    assert excluded_by("MODULE CUMMINS 5579356RX PARTICULATE") == "CUMMINS"
    assert excluded_by("KIT CUMMINS 3977913 GASKET, LUBE OIL") is not None
    assert excluded_by("GASKET MEULLER INDUSTRIES P35708") == "GASKET"


def test_the_reported_term_is_stable_when_several_match():
    """Reason strings must not flip between runs — tallies depend on them."""
    summary = "GASKET CUMMINS 3974127 FOR NEW FLYER BUS"
    assert excluded_by(summary) == "GASKET"
    assert excluded_by(summary) == "GASKET"


# -- the optional opens-from date -------------------------------------------


def test_no_date_means_bypass_the_filter_entirely():
    """The headline behaviour: no input -> no date typing -> all open quotes."""
    for empty in (OpenDateFilter(), OpenDateFilter(opens_from=""), OpenDateFilter(opens_from="   ")):
        assert empty.is_empty
        assert empty.portal_value() is None
        assert empty.summary() == "all open quotes"


def test_a_date_converts_to_the_portal_format():
    f = OpenDateFilter(opens_from="2026-08-05")
    assert f.portal_value() == "08/05/2026"
    assert not f.is_empty
    # The summary names the module too, now that it is selectable — see
    # test_septa_module_choice.py.
    assert f.summary() == "open quotes opening from 2026-08-05"


def test_there_is_no_opens_to_bound():
    """The filter is an open-ended lower bound — an upper one could only hide
    quotes, so the model has no field for it and the form's box is never
    touched."""
    assert "end" not in OpenDateFilter.model_fields
    assert "opens_to" not in OpenDateFilter.model_fields
    assert set(OpenDateFilter.model_fields) == {"opens_from"}


def test_the_scraper_only_knows_the_from_box():
    from app.scrapers.septa.scraper import SEL

    assert "open_date_from_xpath" in SEL
    assert not any("end" in key or "_to_" in key for key in SEL), sorted(SEL)


def test_there_is_no_default_to_today():
    """The old scraper substituted today whenever a run had no other filter,
    silently narrowing an unfiltered run to a single day."""
    from datetime import date

    assert OpenDateFilter().portal_value() is None
    assert OpenDateFilter().summary() != date.today().strftime("%m/%d/%Y")


def test_a_bad_date_is_reported_not_guessed_at():
    for bad in ("05/08/2026", "2026-13-01", "tomorrow", "20260805"):
        try:
            OpenDateFilter(opens_from=bad).portal_value()
        except BadDate as exc:
            assert exc.value == bad
        else:
            raise AssertionError(f"{bad!r} should not have parsed")


# -- the record gate --------------------------------------------------------
#
# `_record_quote` is the single point every scraped row passes through on its
# way to the DB, the spreadsheet and the UI, so the blacklist is applied there.
# These drive it directly — no browser involved.


def _scraper():
    from pathlib import Path

    from app.core import run_manager
    from app.scrapers.septa.scraper import SeptaScraper

    run = run_manager.create_run("septa", Path("/tmp"))
    return SeptaScraper(run["run_id"], OpenDateFilter())


def _row(ref, summary, close="12/01/2026"):
    return {
        "requisition_number": ref,
        "summary": summary,
        "open_date": "08/01/2026",
        "close_date": close,
    }


def test_excluded_rows_never_reach_the_records():
    s = _scraper()
    rows = [
        _row("A1", "GASKET CUMMINS 3974127 FILTER HEAD"),
        _row("A2", "IGBT INFINEON BSM400GA170DLC NO"),
        _row("A3", "LATCH NF 8110774 RH"),
        _row("A4", "STEP SEPTA SPEC S-3544-9 DWG D-4948-F"),
        _row("A5", "BRACKET NEW FLYER 695953 ASSY-ORBSTAR"),
    ]
    kept = [r for r in rows if s._record_quote(r)]
    assert [r["requisition_number"] for r in kept] == ["A2", "A4"], kept
    assert s._excluded_by_summary == 3
    assert dict(s._exclusion_reasons) == {"GASKET": 1, "NF": 1, "NEW FLYER": 1}


def test_excluded_rows_are_not_mirrored_to_the_ui_preview():
    s = _scraper()
    s._record_quote(_row("A1", "LATCH NF 8110774 RH"))
    assert s._records == [] and s._preview == []


def test_a_quote_closing_tomorrow_is_kept():
    """No close-date window: SEPTA keeps every open quote, however urgent.

    The shared MIN_DAYS_UNTIL_CLOSE (7 days) rule used to drop these, which
    withheld exactly the quotes with the least time to act on them.
    """
    from datetime import date, timedelta

    tomorrow = (date.today() + timedelta(days=1)).strftime("%m/%d/%Y")
    s = _scraper()
    assert s._record_quote(_row("A1", "STEP SEPTA SPEC S-3544-9", close=tomorrow)) is True
    assert len(s._records) == 1


def test_a_quote_that_already_closed_is_still_kept():
    """The grid only lists open quotes; a past-looking date is not ours to judge."""
    s = _scraper()
    assert s._record_quote(_row("A1", "STEP SEPTA SPEC S-3544-9", close="01/01/2020")) is True
    assert len(s._records) == 1


def test_an_unreadable_close_date_is_kept():
    s = _scraper()
    for i, close in enumerate(("", "N/A", "see documents")):
        assert s._record_quote(_row(f"A{i}", "STEP SEPTA SPEC S-3544-9", close=close)) is True
    assert len(s._records) == 3


def test_the_close_date_is_exported_exactly_as_scraped():
    s = _scraper()
    s._record_quote(_row("A1", "STEP SEPTA SPEC S-3544-9", close="08/06/2026"))
    assert s._records[0]["close_date"] == "08/06/2026"


def test_the_scraper_no_longer_carries_close_filter_tallies():
    """Leftover counters would imply a filter that no longer runs."""
    s = _scraper()
    assert not hasattr(s, "_skipped_closing_soon")
    assert not hasattr(s, "_kept_unreadable_close")


def test_the_blacklist_is_the_only_thing_that_drops_a_quote():
    s = _scraper()
    s._record_quote(_row("A1", "LATCH NF 8110774 RH", close="08/06/2026"))
    assert s._excluded_by_summary == 1
    assert s._records == []


def test_other_portals_keep_the_shared_close_filter():
    """SEPTA's exemption must not have leaked into the shared rule."""
    from app.core.closing_filter import MIN_DAYS_UNTIL_CLOSE

    assert MIN_DAYS_UNTIL_CLOSE == 7


def test_kept_records_carry_only_the_exported_columns():
    """No niche / matched_terms leftovers now that per-term searching is gone."""
    from app.scrapers.septa.models import EXCEL_COLUMNS

    s = _scraper()
    s._record_quote(_row("A4", "STEP SEPTA SPEC S-3544-9"))
    assert set(s._records[0]) == {attr for attr, _ in EXCEL_COLUMNS}


def test_the_grid_repeating_a_row_across_pages_stores_it_once():
    s = _scraper()
    assert s._record_quote(_row("A4", "STEP SEPTA SPEC S-3544-9")) is True
    assert s._record_quote(_row("A4", "STEP SEPTA SPEC S-3544-9")) is False
    assert len(s._records) == 1


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:
            print(f"FAIL  {name}: {exc}")
            failures += 1
        except Exception as exc:  # noqa: BLE001 — report, don't abort the suite
            print(f"ERROR {name}: {exc.__class__.__name__}: {exc}")
            failures += 1
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
