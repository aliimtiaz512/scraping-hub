"""Regression tests for the SAM bid evaluator (engine.evaluator.evaluate_bid).

Covers the four criteria upgrades:
  Fix 1 — overseas place-of-performance (embassies/consulates/countries) REJECTs
          Rule C service bids.
  Fix 2 — a "rental" primary scope fires Rule B #6 ahead of hardware / Rule C.
  Fix 3 — unlisted service splits by location: US Mainland -> MANUAL_REVIEW,
          outside -> REJECT; plus the NAICS 238xxx Rule C re-run rules.
  Fix 4 — "idiq" is no longer a kill-word (hardware IDIQ contracts PURSUE).

These call ``evaluate_bid`` directly with a fixed config, so they exercise the
engine logic in isolation (no DB / network). Run standalone:

    server/.venv/bin/python server/tests/test_sam_evaluator.py

or under pytest if it is installed.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.scrapers.sam.engine.evaluator import evaluate_bid  # noqa: E402

# kill-words as they stand after Fix 4 (idiq removed).
CONFIG = {"evaluation": {"kill_words": ["market research", "rfi", "sources sought"]}}


# Each case: id, title, naics, description(body), expected decision, expected rule.
CASES = [
    # ---- named regression bids from the spec --------------------------------
    dict(
        bid="191D3226R0039",
        title="HVAC Maintenance Services - U.S. Embassy Jakarta, Indonesia",
        naics="238220",
        body="Place of Performance: Jakarta, Indonesia. Contractor shall service "
             "the chiller and air handling units at the embassy compound.",
        decision="REJECT", rule="C6",  # Rule C service, but outside US Mainland
    ),
    dict(
        bid="19AQMM26R0332",
        title="Facilities Support Services - U.S. Embassy Freetown, Sierra Leone",
        naics="561210",
        body="Place of Performance: Freetown, Sierra Leone.",
        decision="REJECT", rule="none",  # unlisted service, outside -> REJECT
    ),
    dict(
        bid="W911N226QA053",
        title="Rental of Portable Restroom Facilities",
        naics="532490",
        body="The government requires rental of portable restrooms for 12 months.",
        decision="REJECT", rule="B6",  # rental override
    ),
    dict(
        bid="1305M226Q0211",
        title="Purchase of Enclosed Cargo Trailer",
        naics="336212",
        body="Procurement of one enclosed cargo trailer, quantity 1.",
        decision="PURSUE", rule="A",  # hardware
    ),
    dict(
        bid="N0040626Q0409",
        title="CPU Module, Circuit Card Assembly",
        naics="334111",
        body="Supply of CPU module circuit card assemblies.",
        decision="PURSUE", rule="A",  # hardware
    ),

    # ---- Fix 4: IDIQ is no longer a dealbreaker -----------------------------
    dict(
        bid="IDIQ-HW-001",
        title="IDIQ for Supply of Bolts, Nuts and Fasteners",
        naics="332722",
        body="Indefinite delivery indefinite quantity contract for fasteners.",
        decision="PURSUE", rule="A",  # would have been killed pre-Fix-4
    ),

    # ---- Fix 3: US-Mainland unlisted service -> MANUAL_REVIEW ----------------
    dict(
        bid="MR-001",
        title="Braille Transcription Services, Denver, CO",
        naics="541990",
        body="Convert printed manuals to braille for distribution.",
        decision="MANUAL_REVIEW", rule="none",
    ),

    # ---- Fix 3: NAICS 238xxx Rule C re-run rules (US Mainland -> PURSUE) -----
    dict(
        bid="RC6-001",
        title="Mini-Split Air Conditioning Units, Building 500, Fort Sill, OK",
        naics="238220",
        body="Furnish and install mini-split cooling units.",
        decision="PURSUE", rule="C6",
    ),
    dict(
        bid="RC1-001",
        title="Electrical Wiring and Conduit Installation, Fort Hood, TX",
        naics="238210",
        body="Install branch-circuit wiring and conduit.",
        decision="PURSUE", rule="C1",
    ),
    dict(
        bid="RC2-001",
        title="Perimeter Fence Installation, Fort Riley, KS",
        naics="238990",
        body="Install new chain-link perimeter fencing.",
        decision="PURSUE", rule="C2",
    ),

    # ---- Fix 1: diplomatic post in body flips a domestic-looking Rule C ------
    dict(
        bid="EMB-BODY-001",
        title="Generator Repair and Maintenance",
        naics="238210",
        body="Work performed at the American Consulate. Repair the standby "
             "generator and UPS.",
        decision="REJECT", rule="C4",  # Rule C service located overseas
    ),
]


def run_case(c: dict) -> tuple[bool, str]:
    res = evaluate_bid(
        c["bid"], c["body"], CONFIG, naics_code=c["naics"], title=c["title"]
    )
    ok = res["decision"] == c["decision"] and res["rule"] == c["rule"]
    detail = (
        f"{c['bid']:>16}  expected {c['decision']}/{c['rule']:<5}  "
        f"got {res['decision']}/{res['rule']}  [{res['reason']}]"
    )
    return ok, detail


def _make_test(case):
    def _t():
        ok, detail = run_case(case)
        assert ok, detail
    return _t


# Expose each case as a pytest-discoverable test function.
for _c in CASES:
    globals()[f"test_{_c['bid'].replace('-', '_')}"] = _make_test(_c)


if __name__ == "__main__":
    failures = 0
    for c in CASES:
        ok, detail = run_case(c)
        print(("PASS  " if ok else "FAIL  ") + detail)
        failures += 0 if ok else 1
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    sys.exit(1 if failures else 0)
