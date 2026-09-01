"""SAM's binary decision engine — no MANUAL_REVIEW, ever.

Source of truth: `SAM_Binary_Engine_Prompt_and_Criteria.pdf` in the repository
root (v2.0, Rizviz International Impex). The engine used to emit three decision
states; a bid that matched neither Rule B nor Rule C inside the US Mainland came
back MANUAL_REVIEW and went in front of a person. Part A of that spec removes
the state entirely: every bid resolves to PURSUE or REJECT through two new
steps behind the existing ladder —

    4a  Hard Reject Gate      NAICS ranges only, no bid content
    4b  Structural Score      four generic dimensions, then Ollama for the
                              0.40-0.80 band it cannot call

The tests here are written against the spec's sections — B4's gate table, B5's
dimensions and thresholds, and B8's fourteen validation cases — rather than
against the implementation, so a rule that drifts from the document fails.

The design constraint is itself under test. B5 forbids product-specific
keywords: "would this rule still be correct if the specific product changed but
the NAICS and structure stayed the same?" `test_the_scorer_is_blind_to_the_product`
is what holds that promise.

    server/.venv/bin/python -m pytest server/tests/test_sam_binary_engine.py
"""

import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.scrapers.sam.engine import evaluator  # noqa: E402
from app.scrapers.sam.engine.evaluator import evaluate_bid  # noqa: E402

CONFIG = yaml.safe_load(
    (Path(__file__).parent.parent / "app/scrapers/sam/engine/config.yml").read_text()
)["sam"]

BINARY = {"PURSUE", "REJECT"}


def evaluate(title="", naics="", description="", bid_id="TEST-1", **kwargs):
    """One bid through the whole ladder, with the description in the shape the
    engine reads it from (`=== Description ===`, as the scraper writes it)."""
    full_text = f"=== Description ===\n{description}\n" if description else ""
    kwargs.setdefault("binary", True)
    return evaluate_bid(bid_id, full_text, CONFIG, naics_code=naics, title=title, **kwargs)


# =============================================================================
# The point of the change: MANUAL_REVIEW is unreachable
# =============================================================================


@pytest.mark.parametrize("title, naics, description", [
    ("Annual grounds survey", "541611", "Contractor shall survey the grounds."),
    ("Interpreter support services", "541930", "Statement of work attached."),
    ("Widget supply", "332999", "Commercial products. Specification attached."),
    ("Something nobody wrote a rule for", "", ""),
    ("Boiler water treatment service", "238220", "PWS attached."),
    ("NVR cameras brand name or equal", "561621", "Commercial products, QTY 12."),
    ("", "", ""),
])
def test_no_bid_comes_back_as_manual_review(title, naics, description):
    """Acceptance criterion 1 — the whole reason this engine changed."""
    result = evaluate(title=title, naics=naics, description=description)

    assert result["decision"] in BINARY
    assert result["final_decision"] in BINARY


def test_a_ladder_path_that_does_not_resolve_is_coerced_rather_than_leaked(monkeypatch):
    """Belt to the brace. A third state reaching the spreadsheet is the failure
    this change exists to prevent, so it is made impossible rather than
    unlikely — even if a future edit reintroduces a branch that returns one."""
    monkeypatch.setattr(
        evaluator, "_decide",
        lambda *a, **k: {"bid_id": "X", "decision": "MANUAL_REVIEW", "reason": "",
                         "stopped_at_step": 4},
    )

    result = evaluate_bid("X", "", CONFIG, binary=True)

    assert result["decision"] == "REJECT"
    assert result["final_decision"] == "REJECT"


# =============================================================================
# Step 4a — the hard reject gate (spec B4)
# =============================================================================


@pytest.mark.parametrize("naics, rule", [
    ("236220", "Rule B #5"),
    ("237310", "Rule B #5"),
    ("513210", "Rule B #3"),
    ("511120", "Rule B #3"),
    ("611512", "Rule B #9"),
    ("112111", "Out-of-scope"),
    ("114111", "Out-of-scope"),
    ("238320", "Rule B #5"),
    ("238330", "Rule B #5"),
    ("238140", "Rule B #5"),
    ("238170", "Rule B #5"),
    ("238910", "Rule B #5"),
    ("238310", "Rule B #5"),
    ("238370", "Rule B #5"),
    ("561720", "Rule B #10"),
    ("561730", "Out-of-scope"),
    ("561790", "Out-of-scope"),
    ("541380", "Rule B #1"),
    ("541511", "Out-of-scope"),
    ("541513", "Out-of-scope"),
    ("541519", "Out-of-scope"),
    ("115310", "Out-of-scope"),
    ("562991", "Out-of-scope"),
])
def test_every_gated_naics_is_rejected(naics, rule):
    """B4's table in full — each entry, and the rule it cites."""
    reason = evaluator._check_hard_reject_gate(naics)

    assert reason is not None, f"{naics} should be gated"
    assert reason.startswith(rule)


@pytest.mark.parametrize("naics", ["238210", "238220", "238290", "238390"])
def test_the_rule_c_installation_trades_pass_the_gate(naics):
    """The 238 split, which B4 calls out in capitals. Cable, HVAC, industrial
    hardware and "other" are Rule C trades; matching the three-character prefix
    the way 236 and 237 are matched would reject every one of them."""
    assert evaluator._check_hard_reject_gate(naics) is None


def test_the_gate_reads_a_code_with_its_title_attached():
    """The scraper's NAICS field arrives as "541519 — Other Computer Services"
    on some bids and as bare digits on others."""
    assert evaluator._check_hard_reject_gate("541519 — Other Computer Services")
    assert evaluator._check_hard_reject_gate("236220 Commercial Building")


def test_a_missing_naics_does_not_gate_a_bid():
    """No code is not evidence of an excluded code — that bid belongs to the
    scorer, which can still read its title and description."""
    assert evaluator._check_hard_reject_gate("") is None
    assert evaluator._check_hard_reject_gate(None) is None


# =============================================================================
# Step 4b — the structural score (spec B5)
# =============================================================================


@pytest.mark.parametrize("naics, expected", [
    ("332999", 1.00), ("311111", 1.00), ("339999", 1.00),
    ("423450", 1.00), ("424690", 1.00),
    ("238120", 0.85), ("238220", 0.85), ("238350", 0.85), ("238610", 0.85),
    ("334610", 0.65), ("561621", 0.65), ("811310", 0.65),
    ("541611", 0.25), ("561499", 0.25), ("", 0.25),
])
def test_dimension_one_scores_the_naics_band(naics, expected):
    assert evaluator._score_naics_alignment(naics) == expected


def test_the_hardware_adjacent_band_beats_the_manufacturing_range():
    """3346x sits inside 311-339. Read shortest-prefix-first it would score 1.00
    like any other manufacturer, and "hardware-adjacent, verify" would mean
    nothing at all."""
    assert evaluator._score_naics_alignment("334610") == 0.65
    assert evaluator._score_naics_alignment("334516") == 1.00


#: Long enough to clear the 30-character "blank description" penalty, so these
#: cases measure the signal under test rather than the absence of a description.
_COMMERCIAL_PRODUCTS = "This acquisition is for commercial products as defined in FAR 2.101."


@pytest.mark.parametrize("title, description, expected", [
    ("Widget", _COMMERCIAL_PRODUCTS, 0.50),
    ("Widget P/N 12345", _COMMERCIAL_PRODUCTS, 0.80),
    ("Widget P/N 12345 QTY 40", _COMMERCIAL_PRODUCTS, 1.00),
    ("Widget NSN 1234-00-111-2222", _COMMERCIAL_PRODUCTS, 0.80),
    ("Widget brand name or equal", _COMMERCIAL_PRODUCTS, 0.75),
    ("Widget", "This acquisition is for commercial services under FAR 12.", 0.00),
    ("Widget", "See the statement of work attached to this notice for details.", 0.00),
    ("Widget", "", 0.00),
])
def test_dimension_two_scores_the_procurement_structure(title, description, expected):
    assert evaluator._score_procurement_structure(title, description) == pytest.approx(expected)


@pytest.mark.parametrize("title, expected", [
    ("Purchase of assorted widgets", 1.00),
    ("Furnish and deliver widgets", 1.00),
    ("Install replacement units", 0.75),
    ("Upgrade the existing units", 0.75),
    ("Widgets, assorted", 0.50),
    ("Repair and overhaul of units", 0.00),
    ("Annual inspection services", 0.00),
    ("Purchase and repair of units", 0.35),
])
def test_dimension_three_scores_the_primary_verb(title, expected):
    assert evaluator._score_primary_verb(title) == expected


@pytest.mark.parametrize("description, expected", [
    ("A bid with a reasonably long description and nothing special in it.", 0.60),
    ("See the attached technical specification and drawing for details.", 0.90),
    ("", 0.20),
])
def test_dimension_four_scores_the_scope_clarity(description, expected):
    assert evaluator._score_scope_clarity(description) == pytest.approx(expected)


def test_the_total_is_the_weighted_sum_the_spec_specifies():
    scores = evaluator._compute_structural_score(
        title="Purchase of widgets P/N 123 QTY 5",
        description="commercial products. see technical specification.",
        naics="332999",
    )
    expected = (
        0.40 * scores["naics_alignment"]
        + 0.35 * scores["procurement_structure"]
        + 0.15 * scores["primary_verb"]
        + 0.10 * scores["scope_clarity"]
    )

    assert scores["total"] == pytest.approx(expected, abs=1e-4)


def test_the_thresholds_are_the_documented_ones():
    assert evaluator.PURSUE_THRESHOLD == 0.80
    assert evaluator.REJECT_THRESHOLD == 0.40


def test_the_scorer_is_blind_to_the_product():
    """B5's design constraint, made a test: "would this rule still be correct if
    the specific product changed but the NAICS and structure stayed the same?"

    Two bids for entirely unrelated things, identical in every structural
    respect, must score identically. A product keyword anywhere in the scoring
    logic breaks this.
    """
    shape = dict(description="commercial products. technical specification attached.",
                 naics="332999")
    a = evaluator._compute_structural_score(title="Purchase of hydraulic pumps QTY 12", **shape)
    b = evaluator._compute_structural_score(title="Purchase of dental chairs QTY 12", **shape)

    assert a == b


# =============================================================================
# Step 4b — the Ollama band
# =============================================================================


def _mid_band_bid():
    """A bid engineered to land between the thresholds, so the band is exercised
    rather than assumed."""
    return dict(title="Widgets for the facility", naics="238220",
                description="The contractor shall provide widgets to the site as needed.")


def test_a_bid_in_the_uncertain_band_asks_the_resolver():
    asked = {}

    def resolver(**kwargs):
        asked.update(kwargs)
        return {"decision": "PURSUE"}

    result = evaluate(**_mid_band_bid(), resolver=resolver)

    assert asked, "the resolver was never called"
    assert evaluator.REJECT_THRESHOLD < asked["scores"]["total"] < evaluator.PURSUE_THRESHOLD
    assert result["decision"] == "PURSUE"


def test_the_resolver_receives_the_four_dimensions():
    """Step 4 of the spec: the model is not asked to classify the bid, it is
    asked to break a tie the engine has already quantified."""
    captured = {}

    def resolver(**kwargs):
        captured.update(kwargs["scores"])
        return None

    evaluate(**_mid_band_bid(), resolver=resolver)

    for dimension in ("naics_alignment", "procurement_structure", "primary_verb",
                      "scope_clarity", "total"):
        assert dimension in captured


def test_an_unresolved_bid_in_the_band_rejects():
    """The spec's own fallback — `(ollama or {}).get('decision', 'REJECT')`. It
    is the price of a binary contract: with no model reachable, an ambiguous bid
    is dropped rather than shown to anyone."""
    assert evaluate(**_mid_band_bid(), resolver=None)["decision"] == "REJECT"
    assert evaluate(**_mid_band_bid(), resolver=lambda **_: None)["decision"] == "REJECT"


def test_a_resolver_that_will_not_choose_is_treated_as_no_answer():
    result = evaluate(**_mid_band_bid(), resolver=lambda **_: {"decision": "MANUAL_REVIEW"})

    assert result["decision"] == "REJECT"


def test_a_resolver_that_raises_never_fails_the_bid():
    def resolver(**_):
        raise RuntimeError("ollama is down")

    assert evaluate(**_mid_band_bid(), resolver=resolver)["decision"] == "REJECT"


def test_a_confident_bid_never_reaches_the_resolver():
    """Outside the band there is nothing to break — asking would spend a model
    call on a decision already made."""
    calls = []

    def resolver(**_):
        calls.append(1)
        return {"decision": "PURSUE"}

    evaluate(title="Roof survey and inspection", naics="541611",
             description="", resolver=resolver)

    assert not calls


# =============================================================================
# Step 5 — the output schema
# =============================================================================


@pytest.mark.parametrize("title, naics, description", [
    ("Widget P/N 1 QTY 2", "334516", "commercial products"),
    ("Building construction", "236220", "construct a building"),
    ("Market research notice", "334516", ""),
    ("Widgets for the facility", "238220", "The contractor shall provide widgets."),
])
def test_every_bid_carries_the_whole_schema(title, naics, description):
    """Acceptance criterion 5 — all fields present on every bid, whichever step
    decided it."""
    result = evaluate(title=title, naics=naics, description=description)

    for field in ("solicitation_id", "final_decision", "confidence_score",
                  "decision_path", "match_reasons", "rejection_reasons",
                  "score_breakdown"):
        assert field in result, field
    assert 0.0 <= result["confidence_score"] <= 1.0
    assert isinstance(result["match_reasons"], list)
    assert isinstance(result["rejection_reasons"], list)


def test_the_reason_lists_follow_the_decision():
    """Populated when PURSUE, populated when REJECT — never both."""
    pursued = evaluate(title="Widget P/N 1 QTY 2", naics="334516", description="commercial products")
    rejected = evaluate(title="Building construction", naics="236220")

    assert pursued["match_reasons"] and not pursued["rejection_reasons"]
    assert rejected["rejection_reasons"] and not rejected["match_reasons"]


@pytest.mark.parametrize("title, naics, path", [
    ("Market research for widgets", "334516", "step0_killword"),
    ("Widget P/N 1 QTY 2", "334516", "step1_hardware"),
    ("Janitorial cleaning services", "561720", "step2_rule_b"),
    ("Fiber optic cable installation", "238210", "step3_rule_c"),
    ("Flooring work", "238330", "step4a_naics_gate"),
])
def test_the_decision_path_names_the_step_that_decided(title, naics, path):
    assert evaluate(title=title, naics=naics)["decision_path"] == path


def test_the_gate_reports_the_confidence_the_spec_gives_it():
    result = evaluate(title="Some flooring work", naics="238330")

    assert result["decision_path"] == "step4a_naics_gate"
    assert result["confidence_score"] == pytest.approx(0.05)


def test_a_scored_bid_carries_its_breakdown():
    result = evaluate(title="Widgets for the facility", naics="238220",
                      description="The contractor shall provide widgets to the site.")

    assert result["decision_path"] == "step4b_structural_score"
    assert result["score_breakdown"]["total"] == result["confidence_score"]


def test_the_existing_keys_are_untouched():
    """Additive, not a rewrite: `runner.py`, `export.py`, `models.py` and the
    console all read the old names, and the spec's "one targeted change" is
    about not rewriting those consumers."""
    result = evaluate(title="Widget P/N 1 QTY 2", naics="334516", description="commercial products")

    for field in ("bid_id", "decision", "reason", "requirement_type", "rule",
                  "location", "stopped_at_step"):
        assert field in result, field


# =============================================================================
# B8 — the spec's own validation table
# =============================================================================


@pytest.mark.parametrize("naics, title, description, expected, note", [
    ("334516", "Analyzer assembly", "commercial products", "PURSUE", "Hardware NAICS — Rule A"),
    ("333998", "Unit P/N 1234567 QTY 50", "commercial products", "PURSUE", "Hardware + P/N + QTY"),
    ("238220", "HVAC condensate pump replacement", "", "PURSUE", "Rule C #6 + US Mainland"),
    ("238210", "Fiber optic cable installation", "", "PURSUE", "Rule C #1 + US Mainland"),
    ("513210", "Software license renewal", "", "REJECT", "NAICS 513xxx -> Rule B #3"),
    ("236220", "Building construction", "", "REJECT", "NAICS 236xxx -> Rule B #5"),
    ("238330", "Flooring work", "", "REJECT", "NAICS 238330 -> Rule B #5"),
    ("611512", "Flight training", "", "REJECT", "NAICS 611xxx -> Rule B #9"),
    ("561730", "Landscaping", "", "REJECT", "NAICS 561730 -> out-of-scope"),
    ("112111", "Livestock", "", "REJECT", "NAICS 112xxx -> out-of-scope"),
    ("238220", "Boiler water treatment service", "Statement of work attached.",
     "REJECT", "verb=service, structure=SOW"),
    ("541513", "Commercial internet service", "", "REJECT", "NAICS 541513 -> out-of-scope"),
    ("238220", "Galley renovation", "Statement of work attached for the renovation.",
     "REJECT", "verb=construction, SOW present"),
])
def test_the_specs_validation_cases(naics, title, description, expected, note):
    result = evaluate(title=title, naics=naics, description=description)

    assert result["decision"] == expected, f"{note}: {result['reason']}"


def test_the_brand_name_case_reaches_pursue_only_with_a_product_verb():
    """B8's 561621 row, and a documented inconsistency in the spec.

    B8 expects "(any) NVR cameras brand name or equal" on NAICS 561621 to score
    >= 0.80 and PURSUE. Under B5's own weights it cannot:

        d1 = 0.65  (5616x — hardware-adjacent)
        d2 = 0.95  (commercial products +0.50, QTY +0.20, brand name +0.25)
        d3 = 0.50  (no primary verb in the title)
        d4 = 0.90  (specification mentioned)
        total = 0.4(0.65) + 0.35(0.95) + 0.15(0.50) + 0.10(0.90) = 0.7575

    0.7575 lands in the uncertain band, so the bid goes to Ollama rather than
    to an automatic PURSUE. The ceiling for any 5616x bid is 0.85, reachable
    only with d3 = 1.00 — a product verb in the title.

    B5 says weights are adjusted "only if overall accuracy degrades across a
    large batch — not for individual edge cases", so the weights are left as
    specified and the gap is recorded here instead. Both halves are asserted:
    the case as B8 writes it, and the same case with a verb.
    """
    without_verb = evaluate(
        title="NVR cameras brand name or equal QTY 12", naics="561621",
        description="commercial products. See technical specification for details.",
    )
    with_verb = evaluate(
        title="Purchase NVR cameras brand name or equal QTY 12", naics="561621",
        description="commercial products. See technical specification for details.",
        resolver=None,
    )

    assert without_verb["confidence_score"] == pytest.approx(0.7575, abs=1e-3)
    assert evaluator.REJECT_THRESHOLD < without_verb["confidence_score"] < evaluator.PURSUE_THRESHOLD
    assert with_verb["decision"] == "PURSUE"
    assert with_verb["decision_path"] == "step4b_structural_score"


# =============================================================================
# Acceptance criterion 3 — nothing that already decided changes
# =============================================================================


@pytest.mark.parametrize("title, naics, expected", [
    ("Market research for widgets", "334516", "REJECT"),
    ("Sources sought — widgets", "334516", "REJECT"),
    ("SBIR Phase II research", "541713", "REJECT"),
    ("Rental of forklifts", "532412", "REJECT"),
    ("Widget P/N 1234 QTY 10", "332999", "PURSUE"),
    ("Janitorial services", "561720", "REJECT"),
    ("Fiber optic cable installation", "238210", "PURSUE"),
])
def test_the_rules_above_step_four_are_untouched(title, naics, expected):
    """Steps 0 through 3 decided these before the change and must decide them
    the same way now — the new steps sit strictly behind them."""
    result = evaluate(title=title, naics=naics)

    assert result["decision"] == expected


def test_an_unlisted_service_outside_the_mainland_still_rejects_on_location():
    """B7 keeps this branch even though Part A's pseudocode drops it. Sending
    these through the scorer could promote an existing REJECT to PURSUE, which
    acceptance criterion 3 forbids."""
    result = evaluate(
        title="Grounds survey services",
        naics="541611",
        description="Place of performance: Ramstein Air Base, Germany.",
    )

    assert result["decision"] == "REJECT"
    assert result["decision_path"] == "step4_location_gate"


# =============================================================================
# The engine is shared — only SAM asked for a binary answer
# =============================================================================


def test_the_other_portals_keep_their_manual_review():
    """`evaluate_bid` also decides every Philadelphia and Unison bid, and both
    have their own criteria documents, their own MANUAL_REVIEW rows and their
    own amber styling for them. The binary spec is a SAM document, so binary
    mode is opt-in and the shared default is untouched."""
    result = evaluate_bid(
        "PHL-1", "=== Description ===\nConvert printed manuals to braille.\n",
        CONFIG, naics_code="541990", title="Braille Transcription Services, Denver, CO",
    )

    assert result["decision"] == "MANUAL_REVIEW"


def test_sam_asks_for_binary_everywhere_it_evaluates():
    """A run and the ad-hoc endpoint must answer the same way, or the endpoint
    previews a different engine than the one that decides."""
    import inspect

    from app.scrapers.sam import router, runner

    assert "binary=True" in inspect.getsource(runner.execute_run)
    assert "binary=True" in inspect.getsource(router.evaluate_bid_endpoint)
