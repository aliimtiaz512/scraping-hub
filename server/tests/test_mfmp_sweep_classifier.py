"""Classifier tests — MFMP_Niche_Classification_Criteria.md §2-§6, §9.

These run with no browser, no portal and no DB: the scoring functions are pure
and every constant is read from mfmp_niches.yaml, so each rule in the criteria
doc is testable in isolation. That is the main reason scoring.py holds no
literals of its own.

    server/.venv/bin/python server/tests/test_mfmp_sweep_classifier.py

or under pytest if it is installed.
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.scrapers.myflorida.sweep.config import OTHER, load_config  # noqa: E402
from app.scrapers.myflorida.sweep.matching import find_terms  # noqa: E402
from app.scrapers.myflorida.sweep.routing import classify  # noqa: E402
from app.scrapers.myflorida.sweep.scoring import score_all, score_niche  # noqa: E402

CONFIG = load_config()


def totals(title, scope="", codes=()):
    return {k: s.total for k, s in score_all(CONFIG, title, scope, list(codes)).items()}


# -- §2.1 exclusion terms suppress, never penalise ---------------------------

def test_exclusion_suppresses_the_match_it_contains():
    """"3D printing" must not count as a `printing` hit for N3."""
    n3 = CONFIG.niches["N3"]
    hit = find_terms("3D printing services", n3.core_terms, n3.exclusion_terms, n3.stem_map)
    assert hit.matched == [], hit.matched
    assert "printing" in hit.suppressed


def test_exclusion_does_not_suppress_an_independent_occurrence():
    """A real printing scope that also mentions 3D printing still counts."""
    n3 = CONFIG.niches["N3"]
    hit = find_terms(
        "Offset printing of annual reports; no 3D printing required",
        n3.core_terms, n3.exclusion_terms, n3.stem_map,
    )
    assert "printing" in hit.matched, hit


def test_landscape_design_is_not_a_graphic_design_hit():
    assert totals("Landscape Design Services")["N1"] < CONFIG.thresholds["niche_match"]


# -- §3.2 title scoring ------------------------------------------------------

def test_single_core_term_scores_the_first_hit_value():
    score = score_niche(CONFIG, CONFIG.niches["N3"], "Printing", "", [])
    assert score.title == CONFIG.scoring["title"]["core_first"], score.title


def test_second_distinct_core_term_adds_once():
    weights = CONFIG.scoring["title"]
    score = score_niche(CONFIG, CONFIG.niches["N1"], "Graphic Design and Branding", "", [])
    assert score.title == weights["core_first"] + weights["core_additional"], score.title


def test_high_intent_modifier_adds():
    weights = CONFIG.scoring["title"]
    plain = score_niche(CONFIG, CONFIG.niches["N3"], "Printing", "", []).title
    modified = score_niche(CONFIG, CONFIG.niches["N3"], "Printing Services", "", []).title
    assert modified == plain + weights["high_intent_modifier"], (plain, modified)


def test_title_is_capped():
    score = score_niche(
        CONFIG, CONFIG.niches["N4"],
        "Web Development and Software Development Services Implementation", "", [],
    )
    assert score.title == CONFIG.scoring["title"]["max"], score.title


def test_umbrella_only_scores_low_and_flags_deep_read():
    score = score_niche(CONFIG, CONFIG.niches["N4"], "System Modernization", "", [])
    assert score.title == CONFIG.scoring["title"]["umbrella_only"], score.title
    assert score.deep_read_required is True


# -- §3.3 scope scoring ------------------------------------------------------

def test_scope_base_follows_the_distinct_term_table():
    table = CONFIG.scoring["scope"]["base_by_distinct"]
    cases = [
        ("nothing relevant here", 0),
        ("the vendor shall provide printing", 1),
        ("printing and bindery", 2),
        ("printing, bindery and paper stock", 3),
        ("printing, bindery, paper stock and trim size", 4),
    ]
    for scope_text, index in cases:
        score = score_niche(CONFIG, CONFIG.niches["N3"], "", scope_text, [])
        assert score.scope == table[index], (scope_text, score.scope, score.scope_matched)


def test_deliverable_artefact_adds_the_bonus():
    bonus = CONFIG.scoring["scope"]["deliverable_bonus"]
    without = score_niche(CONFIG, CONFIG.niches["N3"], "", "printing", []).scope
    with_artefact = score_niche(
        CONFIG, CONFIG.niches["N3"], "", "printing, press-ready PDF", []
    ).scope
    assert with_artefact >= without + bonus, (without, with_artefact)


# -- §3.1 commodity-code alignment -------------------------------------------

def test_tier_a_code_scores_full_marks():
    score = score_niche(CONFIG, CONFIG.niches["N3"], "", "", ["82121500"])
    assert score.code == CONFIG.scoring["code"]["tier_a"], score.code


def test_absent_code_is_neutral_not_zero():
    """The load-bearing rule from §3.1."""
    score = score_niche(CONFIG, CONFIG.niches["N3"], "", "", [])
    assert score.code == CONFIG.scoring["code"]["absent"], score.code


def test_six_digit_prefix_of_a_tier_a_code():
    """82121599 shares its first six digits with Tier A 82121500."""
    score = score_niche(CONFIG, CONFIG.niches["N3"], "", "", ["82121599"])
    assert score.code == CONFIG.scoring["code"]["prefix6_of_tier_a"], score.code


def test_other_niches_tier_a_needs_this_niches_text_to_score():
    """§3.1 row 4 gates the 10 points on this niche's title/scope firing."""
    code = CONFIG.niches["N3"].tier_a_codes()[0]
    with_text = score_niche(CONFIG, CONFIG.niches["N4"], "Web Development", "", [code])
    without_text = score_niche(CONFIG, CONFIG.niches["N4"], "", "", [code])
    assert with_text.code == CONFIG.scoring["code"]["other_niche_tier_a"], with_text.code
    assert without_text.code == CONFIG.scoring["code"]["unrelated"], without_text.code


def test_best_code_wins_when_several_are_published():
    score = score_niche(CONFIG, CONFIG.niches["N3"], "", "", ["99999999", "82121500"])
    assert score.code == CONFIG.scoring["code"]["tier_a"], score.code


# -- §4 the arithmetic consequences flagged in the plan ----------------------

def test_uncoded_single_core_title_term_does_not_reach_the_threshold():
    """15 + 17 = 32. Scope evidence is required — recall depends on it."""
    result = classify(CONFIG, "AD-1", "Printing", "", [])
    assert result.primary_niche == OTHER, result.primary_niche
    assert result.closest_niche == "N3", result.closest_niche
    expected = CONFIG.scoring["code"]["absent"] + CONFIG.scoring["title"]["core_first"]
    assert result.closest_niche_score == expected, result.closest_niche_score


def test_uncoded_ad_with_scope_evidence_classifies():
    result = classify(CONFIG, "AD-2", "Printing", "brochure and paper stock", [])
    assert result.primary_niche == "N3", (result.primary_niche, result.totals())


def test_an_uncoded_ad_can_never_be_strong():
    """Max uncoded total is 15+25+30 = 70; STRONG starts at 75."""
    ceiling = (
        CONFIG.scoring["code"]["absent"]
        + CONFIG.scoring["title"]["max"]
        + CONFIG.scoring["scope"]["max"]
    )
    assert ceiling < CONFIG.thresholds["strength"]["strong"], ceiling


def test_tier_a_code_alone_classifies_but_only_as_possible():
    result = classify(CONFIG, "AD-3", "Annual Widget Acquisition", "", ["82121500"])
    assert result.primary_niche == "N3", result.totals()
    assert result.match_strength == "POSSIBLE", result.match_strength


# -- §5 routing --------------------------------------------------------------

def test_everything_lands_somewhere():
    """§9.4 — no advertisement is ever rejected or dropped."""
    result = classify(CONFIG, "AD-4", "Janitorial Supplies for Region 3", "", ["47131700"])
    assert result.primary_niche == OTHER, result.totals()
    assert result.other_reason == "NO_NICHE_MATCH"
    assert result.closest_niche is not None


def test_all_six_niches_are_always_scored():
    result = classify(CONFIG, "AD-5", "Website Redesign", "", [])
    assert set(result.scores) == set(CONFIG.niches)


def test_other_carries_every_score_for_threshold_replay():
    result = classify(CONFIG, "AD-6", "Unrelated Procurement", "", [])
    assert result.is_other
    assert len(result.totals()) == 6


# -- §5.1 the N6 hard override -----------------------------------------------

def test_n6_override_beats_a_higher_scoring_software_bid():
    """An electronics scope filed under Software is the worst error possible."""
    title = "Software Development Services"
    scope = "reverse engineering of the legacy controller board, including gerber files"
    result = classify(CONFIG, "AD-7", title, scope, [])
    assert result.primary_niche == "N6", (result.primary_niche, result.totals())
    assert "n6_override_applied" in result.flags


def test_building_electrical_does_not_trigger_n6():
    result = classify(
        CONFIG, "AD-8", "Building Electrical Maintenance", "electrical contractor", []
    )
    assert result.primary_niche != "N6", result.totals()


# -- §6 tie-breaks -----------------------------------------------------------

def test_bi_codes_force_n5_over_n4():
    """§6's fully deterministic row: BI codes decide the N4/N5 dashboard case."""
    result = classify(
        CONFIG, "AD-9", "Dashboard Development",
        "build a dashboard over existing data", ["43232314"],
    )
    assert result.primary_niche == "N5", (result.primary_niche, result.totals())


def test_contested_is_flagged_when_two_niches_are_close():
    result = classify(
        CONFIG, "AD-10", "Website Redesign and Branding",
        "wireframes, style guide, brand standards, web application build", [],
    )
    if result.is_other:
        return
    runner_up = max(
        total for key, total in result.totals().items() if key != result.primary_niche
    )
    if (result.primary_score - runner_up) <= CONFIG.thresholds["contested_gap"]:
        assert "contested" in result.flags, result.flags


# -- §9 rules the implementation must not break ------------------------------

def test_no_scoring_constants_live_in_python():
    """§9.2 — a literal weight in scoring.py would be a bug, not a style issue."""
    import app.scrapers.myflorida.sweep.scoring as scoring

    source = Path(scoring.__file__).read_text(encoding="utf-8")
    source = re.sub(r'""".*?"""', "", source, flags=re.S)
    body = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    magic = re.findall(r"(?<![\w.\[])(?:1[0-9]|[2-9][0-9]|100)(?![\w.])", body)
    assert not magic, f"scoring.py contains hard-coded weights: {magic}"


def test_every_niche_has_a_legal_excel_sheet_name():
    """Excel caps tab names at 31 chars and forbids : \\ / ? * [ ]."""
    forbidden = set(r':\/?*[]')
    for niche in CONFIG.ordered_niches():
        assert len(niche.sheet) <= 31, f"{niche.key} sheet name too long: {niche.sheet}"
        assert not (forbidden & set(niche.sheet)), f"{niche.key} sheet illegal: {niche.sheet}"


def test_sheet_names_are_unique():
    sheets = [n.sheet for n in CONFIG.ordered_niches()]
    assert len(sheets) == len(set(sheets)), sheets


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
