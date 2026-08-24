"""NIGP codes as search terms, and the deduplication that has to come with them.

A niche now searches two lists through the one search box: its keywords, then
its NIGP class-item / UNSPSC codes. That is more searches over the same corner
of the portal, so the same solicitation comes back under several terms as a
matter of course — a code that describes a sector finds much of what the
sector's keywords found. Without deduplication the export would carry the same
bid several times over, and the run would open and download it several times to
get there.

So a bid has an identity and the run holds every identity it has seen. Two
rounds: by solicitation id when links are collected (which is what stops the
second download), and by reference number once the detail page has been read
(which catches the same bid served under two different link shapes).

    server/.venv/bin/python -m pytest server/tests/test_bidnet_nigp_and_dedup.py
"""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import run_manager  # noqa: E402
from app.scrapers.bidnet import niches as catalog  # noqa: E402
from app.scrapers.bidnet import scraper as bidnet  # noqa: E402
from app.scrapers.bidnet.niches import KIND_KEYWORD, KIND_NIGP, SearchTerm  # noqa: E402
from app.scrapers.bidnet.scraper import BidnetScraper, LinkHarvest  # noqa: E402


# =============================================================================
# The catalog
# =============================================================================

#: True while the catalog is deliberately empty — every niche's terms were
#: cleared on 2026-08-20 pending a fresh dataset. The content checks below skip
#: on it rather than being deleted: they are the guard that a niche is not
#: half-configured, and the moment terms are added back they start guarding
#: again with no edit here.
CATALOG_EMPTY = not any(
    entry.get("keywords") or entry.get("nigp_codes")
    for entry in catalog.NICHES.values()
)
_needs_terms = pytest.mark.skipif(
    CATALOG_EMPTY,
    reason="the niche catalog is intentionally empty pending the new dataset",
)


def test_every_niche_keeps_its_identity_and_its_two_term_lists():
    """What the purge must NOT have taken. A niche is its key, label, slug,
    order and notes; the terms are data that comes and goes."""
    assert set(catalog.NICHES) == {
        "graphic_design", "commercial_printing", "software_development",
        "ai_analytics", "pcb_electronics",
    }
    for key, entry in catalog.NICHES.items():
        assert entry.get("label"), f"{key} lost its label"
        assert entry.get("slug"), f"{key} lost its slug"
        assert entry.get("notes"), f"{key} lost its notes"
        assert isinstance(entry.get("keywords"), list), f"{key} lost its keyword list"
        assert isinstance(entry.get("nigp_codes"), list), f"{key} lost its code list"


@_needs_terms
def test_every_niche_has_codes_and_keywords():
    """A niche with codes but no keywords (or the reverse) would silently halve
    what the sector searches."""
    for key, entry in catalog.NICHES.items():
        assert entry.get("keywords"), f"{key} has no keywords"
        assert entry.get("nigp_codes"), f"{key} has no NIGP codes"


@_needs_terms
def test_every_code_is_the_five_digit_class_item_form_the_client_uses():
    """The client's document writes class-item numbers unhyphenated and five
    digits wide: 90640 is class 906, item 40. An earlier catalog hyphenated them
    ("965-46"); either is a real way to write a code, but the run types what is
    stored, so the two must not be mixed in one list."""
    import re

    for key, entry in catalog.NICHES.items():
        for code in entry["nigp_codes"]:
            assert re.fullmatch(r"\d{5}", code), (
                f"{key}: {code!r} is not a five-digit NIGP class-item code"
            )


@_needs_terms
def test_a_leading_zero_is_kept():
    """Unlike NAICS, an NIGP class-item code really can start with a zero —
    03752 is class 037, item 52. Storing it as "3752" would search for a code
    that does not exist, and it is exactly what a spreadsheet does to it."""
    codes = [c for entry in catalog.NICHES.values() for c in entry["nigp_codes"]]
    leading_zero = [c for c in codes if c.startswith("0")]

    assert leading_zero, "expected codes with a leading zero in this catalog"
    assert all(len(c) == 5 for c in leading_zero)


@_needs_terms
def test_keywords_are_stored_lowercased_and_collapsed():
    """The run types them verbatim, so a stray capital or double space is a
    different search from the one intended."""
    for key, entry in catalog.NICHES.items():
        for keyword in entry["keywords"]:
            assert keyword == " ".join(keyword.lower().split()), (
                f"{key}: {keyword!r} is not normalised"
            )


@_needs_terms
def test_no_niche_repeats_a_term():
    """The catalog table is unique on (niche_key, term); a repeat inside one
    niche would cost that niche its whole term list at seed time."""
    for key, entry in catalog.NICHES.items():
        terms = entry["keywords"] + entry["nigp_codes"]
        duplicates = {t for t in terms if terms.count(t) > 1}
        assert not duplicates, f"{key} repeats {sorted(duplicates)}"


def test_a_code_is_never_also_a_keyword():
    """The catalog table is unique on (niche, term), so the same string in both
    lists would cost the niche its terms at seeding time."""
    for key, entry in catalog.NICHES.items():
        overlap = set(entry["keywords"]) & set(entry["nigp_codes"])
        assert not overlap, f"{key} lists {overlap} as both a keyword and a code"


def test_the_kind_is_what_the_logs_name_a_search():
    assert SearchTerm("logo design").label == "KEYWORD"
    assert SearchTerm("965-46", KIND_NIGP).label == "NIGP CODE"


def test_a_bare_string_is_taken_as_a_keyword(tmp_path):
    """Callers that have nothing but terms — the tests here, and anything
    resolving a niche by hand — do not have to know about kinds."""
    run = run_manager.create_run("bidnet", tmp_path, {"niche": "x"})
    instance = BidnetScraper(run["run_id"], ["gasket", SearchTerm("965-46", KIND_NIGP)], None, "X")

    assert [(t.term, t.kind) for t in instance.search_terms] == [
        ("gasket", KIND_KEYWORD), ("965-46", KIND_NIGP),
    ]


# -- the file fallback, for a database that predates the `kind` column ---------


class _Boom:
    """A session whose SELECT raises whatever it was given."""

    def __init__(self, exc):
        self.exc = exc
        self.rolled_back = False

    def execute(self, _statement):
        raise self.exc

    def rollback(self):
        self.rolled_back = True


def test_a_missing_kind_column_falls_back_to_the_catalog_file(caplog):
    from sqlalchemy.exc import ProgrammingError

    caplog.set_level(logging.WARNING)
    exc = ProgrammingError(
        "SELECT …", {}, Exception("column bidnet_niche_keywords.kind does not exist")
    )
    terms = catalog.search_terms_for(_Boom(exc), "graphic_design")

    kinds = [t.kind for t in terms]
    assert kinds == [KIND_KEYWORD] * kinds.count(KIND_KEYWORD) + [KIND_NIGP] * kinds.count(KIND_NIGP)
    # The file's own codes, whatever they currently are — this covers the
    # fallback path, not the contents of the catalog.
    assert [t.term for t in terms if t.kind == KIND_NIGP] == (
        catalog.NICHES["graphic_design"]["nigp_codes"]
    )
    assert any("add_bidnet_niche_kind.sql" in r.getMessage() for r in caplog.records)


def test_a_database_that_is_simply_down_is_not_swallowed():
    """The fallback is for one recoverable shape. A dropped connection has to
    keep raising — the router turns it into a 503, and quietly serving the file's
    terms instead would hide an outage behind a working-looking run."""
    from sqlalchemy.exc import OperationalError

    exc = OperationalError("SELECT …", {}, Exception("could not connect to server"))
    with pytest.raises(OperationalError):
        catalog.search_terms_for(_Boom(exc), "graphic_design")


# =============================================================================
# Bid identity
# =============================================================================


def test_the_two_href_shapes_of_one_solicitation_share_an_id():
    """BidNet serves the same bid both ways, and a run that searches keywords and
    then codes meets both. Comparing URLs would make them two bids: opened twice,
    downloaded twice, exported twice."""
    view = "https://www.bidnetdirect.com/private/supplier/interception/view-notice/444124954092"
    open_ = (
        "https://www.bidnetdirect.com/private/supplier/interception/"
        "open-solicitation/444124954092?target=view"
    )

    assert BidnetScraper._bid_key(view) == BidnetScraper._bid_key(open_) == "444124954092"


def test_an_unrecognised_link_shape_keeps_bids_apart():
    """Falling back to the URL is deliberate: an unknown shape must not collapse
    two different bids into one. A duplicate row is recoverable; a lost bid is
    not."""
    a = BidnetScraper._bid_key("https://www.bidnetdirect.com/some/new/path/alpha")
    b = BidnetScraper._bid_key("https://www.bidnetdirect.com/some/new/path/beta")

    assert a != b


def test_a_reference_number_is_compared_on_what_makes_it_the_same_bid():
    assert BidnetScraper._reference_key("RFP 2026-014") == BidnetScraper._reference_key("rfp2026-014")
    assert BidnetScraper._reference_key("") == ""


# =============================================================================
# The run: keywords then codes, deduplicated
# =============================================================================


TERMS = [
    SearchTerm("graphic design"),
    SearchTerm("logo design"),
    SearchTerm("965-46", KIND_NIGP),
    SearchTerm("915-48", KIND_NIGP),
]

VIEW = "https://b/private/supplier/interception/view-notice/{}"
OPEN = "https://b/private/supplier/interception/open-solicitation/{}?target=view"

# What each term's search returns. "965-46" re-finds bid 111 (the same link) and
# bid 222 (the *other* href shape of it), plus one bid of its own.
RESULTS = {
    "graphic design": [VIEW.format(111), VIEW.format(222)],
    "logo design": [VIEW.format(111)],
    "965-46": [VIEW.format(111), OPEN.format(222), VIEW.format(333)],
    "915-48": [],
}


@pytest.fixture
def driven(monkeypatch, tmp_path):
    run = run_manager.create_run("bidnet", tmp_path, {"niche": "x", "niche_label": "X"})
    instance = BidnetScraper(run["run_id"], list(TERMS), None, "Graphic Design")
    current = {"term": None}
    exported: list[list[dict]] = []

    def search(term):
        current["term"] = term

    def collect_links():
        links = RESULTS[current["term"]]
        return LinkHarvest(links=list(links), rows_detected=len(links), rows_parsed=len(links))

    def process_bid(link):
        bid_id = BidnetScraper._bid_key(link)
        return {
            "reference_number": f"RFP-{bid_id}",
            "title": f"Solicitation {bid_id}",
            "detail_url": link,
            "documents": [],
        }

    for name, value in {
        "start_driver": lambda *a, **k: None,
        "login": lambda: None,
        "open_filtered_session": lambda: None,
        "_ensure_filters_live": lambda: None,
        "_ensure_first_result_page": lambda: True,
        "ensure_logged_in": lambda: None,
        "search": search,
        "result_count": lambda: len(RESULTS[current["term"]]),
        "filter_member_agency": lambda: None,
        "confirm_filters_active": lambda keyword="": None,
        "collect_links": collect_links,
        "process_bid": process_bid,
        "_write_master_excel": lambda records: exported.append(list(records)),
        "_save_run_row": lambda: None,
        "cleanup": lambda: None,
    }.items():
        monkeypatch.setattr(instance, name, value)

    monkeypatch.setattr(bidnet.export, "save_bids", lambda run, records: len(records))
    monkeypatch.setattr(bidnet, "archive_run", lambda run_id: None)
    monkeypatch.setattr(bidnet, "notify_scrape_completion", lambda *a, **k: None)
    monkeypatch.setattr(bidnet.run_manager, "remove_empty_folder", lambda run_id: None)
    return instance, exported


def test_codes_are_searched_in_the_same_box_after_the_keywords(driven, caplog):
    caplog.set_level(logging.INFO)
    instance, _ = driven
    instance.run()

    log = "\n".join(r.getMessage() for r in caplog.records)
    assert '[SEARCH EXECUTING]: (1/4) Niche: Graphic Design | Input Type: KEYWORD ' \
           '| Term: "graphic design"' in log
    assert '[SEARCH EXECUTING]: (3/4) Niche: Graphic Design | Input Type: NIGP CODE ' \
           '| Term: "965-46"' in log


def test_a_bid_found_by_a_keyword_and_a_code_is_exported_once(driven):
    """111 is returned by two keywords and one code — four sightings, one row."""
    instance, exported = driven
    instance.run()

    rows = [r for r in exported[0] if r["reference_number"] == "RFP-111"]
    assert len(rows) == 1


def test_the_row_credits_every_term_that_found_it(driven):
    """Dropping the duplicate must not drop the evidence that the code search
    earned its place — otherwise there is no way to tell a code that contributes
    from one that never matches anything."""
    instance, exported = driven
    instance.run()

    row = next(r for r in exported[0] if r["reference_number"] == "RFP-111")
    assert row["matched_keyword"] == "graphic design, logo design, 965-46"


def test_the_other_href_shape_of_a_known_bid_is_not_a_second_row(driven):
    """222 is found as /view-notice by a keyword and as /open-solicitation by a
    code. One solicitation, one row, both terms named."""
    instance, exported = driven
    instance.run()

    rows = [r for r in exported[0] if r["reference_number"] == "RFP-222"]
    assert len(rows) == 1
    assert rows[0]["matched_keyword"] == "graphic design, 965-46"


def test_a_code_that_finds_something_new_still_contributes_it(driven):
    """Deduplication must not turn into "codes never add anything" — 333 is only
    reachable through the NIGP search."""
    instance, exported = driven
    instance.run()

    assert any(r["reference_number"] == "RFP-333" for r in exported[0])
    assert len(exported[0]) == 3        # 111, 222, 333 — and nothing twice


def test_the_run_reports_what_each_search_added(driven, caplog):
    caplog.set_level(logging.INFO)
    instance, _ = driven
    instance.run()

    log = "\n".join(r.getMessage() for r in caplog.records)
    # The keyword that found only what the first one had: nothing new.
    assert "[RESULT]: 1 total bids found (0 new, 1 duplicates skipped)" in log
    # The code: three sightings, one of them new.
    assert "[RESULT]: 3 total bids found (1 new, 2 duplicates skipped)" in log
    assert "[DEDUPLICATION]" in log


def test_the_dedup_tallies_reach_the_run_record(driven):
    instance, _ = driven
    instance.run()

    run = run_manager.get_run(instance.run_id)
    assert run["unique_bid_ids"] == 3
    assert instance._link_duplicates == 3      # 111×2, 222×1 re-sightings


# -- the second round: identity read off the detail page ----------------------


def test_two_links_that_turn_out_to_be_one_bid_are_one_row(monkeypatch, driven, caplog):
    """The case the link round cannot catch: different solicitation ids that the
    detail pages reveal to be the same reference number."""
    caplog.set_level(logging.INFO)
    instance, exported = driven
    monkeypatch.setattr(
        instance, "process_bid",
        lambda link: {
            "reference_number": "RFP 2026-014",       # every link, one bid
            "title": "Citywide signage",
            "detail_url": link,
            "documents": [],
        },
    )
    instance.run()

    assert len(exported[0]) == 1
    assert exported[0][0]["matched_keyword"] == "graphic design, logo design, 965-46"
    assert any("[DUPLICATE SKIPPED]" in r.getMessage() for r in caplog.records)
    assert instance._duplicates_skipped == 2


def test_bids_whose_reference_could_not_be_read_stay_separate(monkeypatch, driven):
    """An unreadable reference is not evidence of sameness. Keying on it would
    merge every failed extraction in the run into a single row and report the
    rest as duplicates."""
    instance, exported = driven
    monkeypatch.setattr(
        instance, "process_bid",
        lambda link: {
            "reference_number": "", "title": "", "detail_url": link,
        },
    )
    instance.run()

    assert len(exported[0]) == 3
    assert instance._duplicates_skipped == 0
