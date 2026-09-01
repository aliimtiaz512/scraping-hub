"""Ollama evaluation wall for MANUAL_REVIEW SAM bids.

This module is the ONLY place a local Ollama model is consulted. It sits behind
the deterministic rule engine (engine/evaluator.py): the runner calls
``ollama_evaluate`` only for bids the rule engine could not decide (decision ==
MANUAL_REVIEW) and, if Ollama resolves it confidently, the result is upgraded to
PURSUE or REJECT before it is stored. Any failure (disabled, timeout, HTTP error,
malformed response) returns ``None`` so MANUAL_REVIEW is preserved untouched.

CRITICAL CONSTRAINT — Ollama must NEVER receive raw bid text (``full_text``).
``full_text`` is 100,000+ characters of FAR boilerplate that poisons every
classification. Ollama receives ONLY a structured brief of ~400 tokens built by
``build_brief``; ``full_text`` is read exclusively by ``get_description_opening``
to lift the first 300 characters of the description section — never attachment
content. This is non-negotiable.
"""

import logging
import os
import re

import requests

logger = logging.getLogger(__name__)

# -- configuration (all overrideable via env, no redeploy needed) -------------
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "30"))  # seconds
OLLAMA_ENABLED = os.getenv("OLLAMA_ENABLED", "true").lower() == "true"


# -- brief extraction ---------------------------------------------------------
def get_description_opening(full_text: str, chars: int = 300) -> str:
    """Return the first ``chars`` characters of the ``=== Description ===`` section only.

    Guaranteed to never read attachment content, even if the description is
    empty: the slice is bounded by the next ``===`` section marker.
    """
    desc_marker = "=== Description ==="
    desc_start = full_text.find(desc_marker)
    if desc_start == -1:
        raw = full_text[:chars]  # No marker — start from the beginning.
    else:
        content_start = full_text.find("\n", desc_start) + 1
        next_section = full_text.find("===", content_start)
        raw = (
            full_text[content_start:next_section]
            if next_section != -1
            else full_text[content_start:]
        )
    return raw[:chars].replace("\n", " ").strip()


# -- NAICS category mapping ---------------------------------------------------
HW_NAICS_RANGES = list(range(311, 340)) + [423, 424]
FOOD_NAICS_START = ["311", "312"]


def _get_naics_category(naics_code: str) -> str:
    prefix = naics_code[:3] if naics_code else ""
    if not prefix.isdigit():
        return "UNKNOWN"
    n = int(prefix)
    if any(naics_code.startswith(f) for f in FOOD_NAICS_START):
        return "FOOD (check Rule B #15)"
    if n in HW_NAICS_RANGES:
        return "HARDWARE (manufacturing/wholesale)"
    return "SERVICE / CONSTRUCTION"


# -- title signal extraction --------------------------------------------------
SERVICE_VERBS = {
    "repair", "overhaul", "inspect", "maintain", "maintenance",
    "calibrate", "clean", "cleaning", "lease", "rent", "rental",
    "manage", "audit", "train", "training", "demolish",
    "construct", "build", "service", "services",
}
HARDWARE_VERBS = {
    "purchase", "supply", "procurement", "procure", "acquire",
    "acquisition", "provide", "furnish", "deliver",
}

PN_PATTERN = re.compile(
    r"\bP/?N\b|\bNSN\b|\bNIIN\b|\bpart\s+no\b|\bpart\s+number\b", re.IGNORECASE
)
QTY_PATTERN = re.compile(r"\bQTY\b|\bquantity\b", re.IGNORECASE)


def _extract_title_signals(title: str) -> dict:
    words = re.findall(r"\b\w+\b", title.lower())
    primary_verb = "NONE DETECTED"
    for word in words:
        if word in SERVICE_VERBS:
            primary_verb = f"SERVICE verb — {word}"
            break
        if word in HARDWARE_VERBS:
            primary_verb = f"HARDWARE verb — {word}"
            break
    return {
        "primary_verb": primary_verb,
        "has_pn": bool(PN_PATTERN.search(title)),
        "has_qty": bool(QTY_PATTERN.search(title)),
    }


# -- description opening signals ----------------------------------------------
SOW_PATTERN = re.compile(
    r"statement\s+of\s+work|performance\s+work\s+statement|\bPWS\b|\bSOW\b",
    re.IGNORECASE,
)


def _extract_desc_signals(desc_opening: str) -> dict:
    lo = desc_opening.lower()
    return {
        "has_commercial_products": "commercial products" in lo,
        "has_commercial_services": "commercial services" in lo,
        "has_sow_pws": bool(SOW_PATTERN.search(desc_opening)),
    }


# -- brief assembly -----------------------------------------------------------
RULE_B = """
1. Maintenance, Repair & Inspection Services   11. Lease of Equipment
2. Management Services                         12. Engineering Support Services
3. Management Software                         13. Hotel Room Booking & Lodging
4. Audit                                       14. Yellow Ribbon
5. Construction & Demolition Services          15. Food Items
6. Rental of Equipment                         16. Religious & Education Coordinator
7. Waste Management Services                    17. Real Estate
8. Promotional Services                        18. Aircraft Lavatory Services
9. Training Services                           19. Marine Vessel Upgrade
10. Custodial Services                         20. Research & Development"""

RULE_C = """
1. Cable Installation                          7. Industrial Hardware Installation
2. Fence Installation                          8. Roofing Install/Repair/Maintenance
3. Furniture Installation                      9. Door / Window Installation
4. UPS / Generator Repair & Maint             10. AV Equipment Installation
5. IT Hardware/Software Install               11. Storage Rack/Shelving Installation
6. HVAC Install/Repair/Maintenance"""


def build_brief(title, naics_code, naics_title, full_text, location, stopped_at_step,
                scores=None) -> str:
    """Assemble the ~400-token structured brief Ollama receives.

    ``full_text`` is read only to lift the first 300 chars of the description
    section; its attachment content never reaches this string.

    ``scores`` is the structural engine's breakdown for a bid in the uncertain
    0.40-0.80 band (spec Part A step 4). Passing it changes what the model is
    being asked: not "classify this bid" but "the engine scored these four
    dimensions and landed between its thresholds — break the tie". Omitted,
    the brief is exactly what it was, so every existing caller is unaffected.
    """
    desc_opening = get_description_opening(full_text, chars=300)
    ts = _extract_title_signals(title)
    ds = _extract_desc_signals(desc_opening)
    cat = _get_naics_category(naics_code)
    score_section = _score_section(scores)
    return f"""NOTICE TITLE: {title}
NAICS: {naics_code} — {naics_title}
NAICS CATEGORY: {cat}
PLACE OF PERFORMANCE: {location}

TITLE SIGNALS:
  Primary verb        : {ts['primary_verb']}
  Part number / NSN   : {'YES' if ts['has_pn'] else 'NO'}
  Quantity (QTY)      : {'YES' if ts['has_qty'] else 'NO'}

DESCRIPTION SIGNALS (first 300 chars only — not full text):
  Commercial products phrase : {'YES' if ds['has_commercial_products'] else 'NO'}
  Commercial services phrase : {'YES' if ds['has_commercial_services'] else 'NO'}
  SOW / PWS present          : {'YES' if ds['has_sow_pws'] else 'NO'}

DESCRIPTION OPENING:
{desc_opening}

WHY THE RULE ENGINE STOPPED: {stopped_at_step}
(Bid matched neither Rule B nor Rule C)
{score_section}

RULE B — EXCLUDED SERVICES (REJECT, any location):
{RULE_B}

RULE C — ALLOWED SERVICES (PURSUE if US Mainland, REJECT if outside):
{RULE_C}

DECISION LOGIC:
  Hardware/material supply    → PURSUE (Rule A)
  Matches Rule B              → REJECT
  Matches Rule C + US         → PURSUE
  Matches Rule C + outside    → REJECT
  No match + US Mainland      → MANUAL_REVIEW
  No match + outside US       → REJECT

Respond in EXACTLY this format — no other text:
DECISION: <PURSUE|REJECT|MANUAL_REVIEW>
RULE: <Rule A | Rule B #N — Name | Rule C #N — Name | none>
REASON: <one sentence, max 20 words>
CONFIDENCE: <HIGH|MEDIUM|LOW>"""


def _score_section(scores) -> str:
    """The structural breakdown, as the spec's SCORES block — or nothing.

    Empty when no scores were passed, so a brief built the old way is byte for
    byte what it was.
    """
    if not scores:
        return ""
    return f"""
PRE-COMPUTED SCORES (from structural engine):
  NAICS alignment    : {scores.get('naics_alignment', 0):.2f} (weight 40%)
  Procurement struct : {scores.get('procurement_structure', 0):.2f} (weight 35%)
  Primary verb       : {scores.get('primary_verb', 0):.2f} (weight 15%)
  Scope clarity      : {scores.get('scope_clarity', 0):.2f} (weight 10%)
  COMBINED SCORE     : {scores.get('total', 0):.2f} (uncertain band: 0.40-0.80)
Engine is uncertain. Use bid context to decide PURSUE or REJECT.
"""


# -- response parsing ---------------------------------------------------------
REASON_MAP = {
    "PURSUE": "Hardware/material requirement — pursued regardless of location (Rule A)",
    "REJECT_RULE_B": "Excluded service category ({rule}) — rejected regardless of location",
    "REJECT_RULE_C": "Allowed service (Rule C) but performed outside US Mainland",
    "REJECT_OUTSIDE": "Service not in allowed/excluded list + performed outside US Mainland",
    "MANUAL_REVIEW": "Service not in allowed or excluded list — manual review required",
}

VALID_DECISIONS = {"PURSUE", "REJECT", "MANUAL_REVIEW"}


def _parse_ollama_response(raw: str) -> dict | None:
    """Parse Ollama's 4-line response into a result dict, or ``None`` if malformed.

    A malformed/absent DECISION returns ``None`` so the caller preserves
    MANUAL_REVIEW rather than acting on garbage.
    """
    lines: dict[str, str] = {}
    for line in raw.strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            lines[key.strip().upper()] = val.strip()

    decision = lines.get("DECISION", "").upper()
    rule = lines.get("RULE", "none")
    confidence = lines.get("CONFIDENCE", "LOW").upper()

    if decision not in VALID_DECISIONS:
        logger.warning(f"Ollama malformed decision: {decision!r}")
        return None
    # MANUAL_REVIEW is no longer a decision the engine can emit (the binary
    # spec). A model that answers with it has declined to choose, which is the
    # same thing as not answering — the caller then applies its own fallback.
    if decision == "MANUAL_REVIEW":
        logger.info("Ollama declined to choose — treated as no answer")
        return None

    if confidence not in {"HIGH", "MEDIUM", "LOW"}:
        confidence = "LOW"

    if decision == "PURSUE":
        reason = REASON_MAP["PURSUE"]
    elif decision == "REJECT":
        if "Rule B" in rule:
            reason = REASON_MAP["REJECT_RULE_B"].format(rule=rule)
        elif "Rule C" in rule:
            reason = REASON_MAP["REJECT_RULE_C"]
        else:
            reason = REASON_MAP["REJECT_OUTSIDE"]
    else:
        reason = REASON_MAP["MANUAL_REVIEW"]

    return {
        "decision": decision,
        "reason": reason,
        "rule": rule,
        "confidence": confidence,
        "source": "ollama",
        "requirement_type": "HARDWARE" if decision == "PURSUE" else "SERVICE",
        "stopped_at_step": "ollama_wall",
    }


# -- public entry point -------------------------------------------------------
def ollama_evaluate(title, naics_code, naics_title, full_text, result, scores=None) -> dict | None:
    """Consult Ollama for one MANUAL_REVIEW bid.

    Builds a structured brief (~400 tokens), calls Ollama, parses the response,
    and returns a result dict on success or ``None`` on timeout/error/disabled.
    Ollama NEVER receives ``full_text`` directly — never raises; a failure
    silently preserves MANUAL_REVIEW.
    """
    if not OLLAMA_ENABLED:
        logger.debug("Ollama disabled — keeping MANUAL_REVIEW")
        return None

    location = result.get("location", "US_MAINLAND")
    stopped_at = result.get("stopped_at_step", "unknown")

    brief = build_brief(
        title,
        naics_code,
        naics_title,
        full_text,  # read here for extraction only — NOT sent to Ollama
        location,
        stopped_at,
        scores,
    )

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": brief,  # NOT full_text
        "stream": False,
        "options": {"temperature": 0, "num_predict": 80},
    }

    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        logger.debug(f"Ollama response for [{title[:60]}]: {raw}")
        return _parse_ollama_response(raw)
    except requests.Timeout:
        logger.warning(f"Ollama timeout: {title[:60]} — keeping MANUAL_REVIEW")
        return None
    except Exception as exc:  # noqa: BLE001 — never propagate; preserve MANUAL_REVIEW
        logger.error(f"Ollama error [{title[:60]}]: {exc}")
        return None
