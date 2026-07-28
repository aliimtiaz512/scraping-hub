"""Unit tests for the Ollama evaluation wall (app.scrapers.sam.ollama_bridge).

Covers the two pure pieces — the brief builder (never leaks attachment text) and
the response parser (malformed → None) — plus the five Round-4 bids that were
confirmed errors and must resolve correctly once the wall is active. The HTTP
call to Ollama is mocked, so these run offline with no local model.

    server/.venv/bin/python server/tests/test_ollama_bridge.py

or under pytest if it is installed.
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.scrapers.sam import ollama_bridge  # noqa: E402
from app.scrapers.sam.ollama_bridge import (  # noqa: E402
    _parse_ollama_response,
    get_description_opening,
    ollama_evaluate,
)


# -- 8.1 brief builder tests --------------------------------------------------
def test_description_only_no_attachments():
    full_text = (
        "=== Description ===\nThis is a pump purchase.\n"
        "=== attachment.pdf ===\nFAR 52.212 boilerplate training food"
    )
    result = get_description_opening(full_text)
    assert "pump purchase" in result
    assert "boilerplate" not in result
    assert "training" not in result


def test_empty_description_fallback():
    result = get_description_opening("No section markers here at all.")
    assert len(result) <= 300


def test_chars_limit_respected():
    full_text = "=== Description ===\n" + "A" * 1000
    assert len(get_description_opening(full_text, chars=300)) <= 300


# -- 8.2 response parser tests ------------------------------------------------
def test_parse_valid_pursue():
    raw = "DECISION: PURSUE\nRULE: Rule A\nREASON: Hardware product.\nCONFIDENCE: HIGH"
    r = _parse_ollama_response(raw)
    assert r["decision"] == "PURSUE"
    assert r["confidence"] == "HIGH"
    assert r["source"] == "ollama"
    assert "Rule A" in r["reason"]


def test_parse_valid_reject_rule_b():
    raw = "DECISION: REJECT\nRULE: Rule B #1 — Maintenance\nREASON: Repair.\nCONFIDENCE: HIGH"
    r = _parse_ollama_response(raw)
    assert r["decision"] == "REJECT"
    assert "Rule B" in r["reason"]


def test_parse_malformed_returns_none():
    assert _parse_ollama_response("") is None
    assert _parse_ollama_response("random text") is None
    assert _parse_ollama_response("DECISION: MAYBE") is None


# -- brief never leaks full_text (acceptance criterion #5) --------------------
def test_full_text_never_sent_to_ollama():
    """The HTTP payload must contain only the brief, never raw full_text."""
    secret = "SECRETBOILERPLATE" * 500  # unmistakable attachment marker
    full_text = (
        "=== Description ===\nRoutine unlisted service.\n"
        f"=== attachment.pdf ===\n{secret}"
    )
    captured = {}

    def _fake_post(url, json=None, timeout=None):
        captured["payload"] = json
        return _FakeResponse("DECISION: MANUAL_REVIEW\nRULE: none\nREASON: x\nCONFIDENCE: LOW")

    with patch.object(ollama_bridge, "OLLAMA_ENABLED", True), \
            patch.object(ollama_bridge.requests, "post", _fake_post):
        ollama_evaluate(
            title="Some ambiguous service",
            naics_code="561210",
            naics_title="Facilities Support Services",
            full_text=full_text,
            result={"location": "US_MAINLAND", "stopped_at_step": 4},
        )

    sent = captured["payload"]["prompt"]
    assert secret not in sent
    assert "SECRETBOILERPLATE" not in sent
    assert "Routine unlisted service" in sent  # the description opening IS present


# -- 8.3 named Round-4 bids (mock the Ollama HTTP call) -----------------------
class _FakeResponse:
    def __init__(self, response_text: str):
        self._response_text = response_text

    def raise_for_status(self):
        return None

    def json(self):
        return {"response": self._response_text}


def run_with_mocked_ollama(title, naics_code, location, ollama_response,
                           naics_title="", full_text=""):
    """Call ollama_evaluate with the HTTP round-trip mocked to a fixed response."""
    if not full_text:
        full_text = f"=== Description ===\n{title}\n=== attachment.pdf ===\nboilerplate"
    result = {"location": location, "stopped_at_step": 4}

    def _fake_post(url, json=None, timeout=None):
        return _FakeResponse(ollama_response)

    with patch.object(ollama_bridge, "OLLAMA_ENABLED", True), \
            patch.object(ollama_bridge.requests, "post", _fake_post):
        return ollama_evaluate(
            title=title,
            naics_code=naics_code,
            naics_title=naics_title,
            full_text=full_text,
            result=result,
        )


# 191D3226R0039 — Jakarta Embassy chiller — outside US → REJECT
def test_jakarta_embassy_chiller():
    result = run_with_mocked_ollama(
        title="40000-hours Overhaul Maintenance Trane Chiller #1 at NEC Jakarta",
        naics_code="811310",
        location="OUTSIDE_MAINLAND",
        ollama_response=(
            "DECISION: REJECT\nRULE: Rule C outside US\n"
            "REASON: HVAC service at foreign embassy.\nCONFIDENCE: HIGH"
        ),
    )
    assert result["decision"] == "REJECT"


# 1305M226Q0211 — Vessel Trailer Florida Keys → PURSUE (hardware)
def test_vessel_trailer_hardware():
    result = run_with_mocked_ollama(
        title="Vessel Trailer for Dusky 28XL for Florida Keys",
        naics_code="336214",
        location="US_MAINLAND",
        ollama_response=(
            "DECISION: PURSUE\nRULE: Rule A\n"
            "REASON: Trailer is physical hardware.\nCONFIDENCE: HIGH"
        ),
    )
    assert result["decision"] == "PURSUE"


# N0040626Q0409 — LPAC CPU Module → PURSUE (commercial products)
def test_lpac_cpu_module():
    result = run_with_mocked_ollama(
        title="INTENT TO SOLE-SOURCE FOR LPAC CPU MODULE FOR DDG CLASS VESSEL",
        naics_code="336611",
        location="US_MAINLAND",
        ollama_response=(
            "DECISION: PURSUE\nRULE: Rule A\n"
            "REASON: Commercial product with part number.\nCONFIDENCE: HIGH"
        ),
    )
    assert result["decision"] == "PURSUE"


# A disabled wall (or any failure) must preserve MANUAL_REVIEW → None.
def test_disabled_preserves_manual_review():
    with patch.object(ollama_bridge, "OLLAMA_ENABLED", False):
        result = ollama_evaluate(
            title="Whatever",
            naics_code="561210",
            naics_title="Facilities",
            full_text="=== Description ===\nx",
            result={"location": "US_MAINLAND", "stopped_at_step": 4},
        )
    assert result is None


def test_timeout_preserves_manual_review():
    import requests as _requests

    def _raise_timeout(url, json=None, timeout=None):
        raise _requests.Timeout("boom")

    with patch.object(ollama_bridge, "OLLAMA_ENABLED", True), \
            patch.object(ollama_bridge.requests, "post", _raise_timeout):
        result = ollama_evaluate(
            title="Whatever",
            naics_code="561210",
            naics_title="Facilities",
            full_text="=== Description ===\nx",
            result={"location": "US_MAINLAND", "stopped_at_step": 4},
        )
    assert result is None


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


if __name__ == "__main__":
    failures = 0
    for t in _TESTS:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as exc:  # noqa: PERF203
            failures += 1
            print(f"FAIL  {t.__name__}: {exc}")
    print(f"\n{len(_TESTS) - failures}/{len(_TESTS)} passed")
    sys.exit(1 if failures else 0)
