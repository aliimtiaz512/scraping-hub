"""The Ollama resolution layer for MFMP bids the rules could not decide.

This is the only place MFMP consults a local model, and it sits behind the
deterministic engine exactly as SAM's does: `evaluation.evaluate` classifies,
and only a bid that came back MANUAL_REVIEW reaches `resolve` here. A confident
answer upgrades the bid to PURSUE or REJECT before anything is written down; any
failure — disabled, timeout, HTTP error, malformed reply — returns None and the
bid stays MANUAL_REVIEW, which the spreadsheet then tints yellow for a person.

**Unlike SAM's bridge, this one does read the attachments.** SAM's constraint is
that its `full_text` is 100,000 characters of FAR boilerplate that poisons any
classification, so its model receives only a structured brief. MFMP's Tier 3 is
the opposite problem: §2 of the criteria found the client keeping and rejecting
visually similar construction bids, which means the deciding facts — contract
value, required Florida licences, whether the work is on-site skilled labour —
exist *only* in the scope-of-work document. A brief built from the title would
reproduce the ambiguity the tier exists to resolve.

So the documents are read, and then bounded: `DOCUMENT_BUDGET` characters,
taken from the files most likely to carry scope, with the boilerplate-heavy tail
of each dropped. What the model sees is still a brief — it is just a brief with
evidence in it.

Configuration is the same env-driven set SAM uses (`OLLAMA_URL`, `OLLAMA_MODEL`,
`OLLAMA_TIMEOUT`, `OLLAMA_ENABLED`), so one Ollama deployment serves both.

NOTE — the prompt below is this module's own. The criteria document ends "This
document accompanies the MFMP resolution-layer prompt already provided
separately", and that prompt was not in the repository or the request. What is
here is written from §5's own extraction list, and is meant to be replaced
verbatim if the client's wording differs.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# -- configuration (shared with the SAM bridge; env-overridable) --------------
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))  # seconds
OLLAMA_ENABLED = os.getenv("OLLAMA_ENABLED", "true").lower() == "true"

#: How much attachment text reaches the model. Generous next to SAM's 300-char
#: description slice because here the document *is* the evidence — but bounded,
#: because a 200-page specification costs minutes per bid and the deciding facts
#: (value, licences, labour) are stated in the first pages of a scope of work.
DOCUMENT_BUDGET = 6000

#: Per-file share of that budget, so one long PDF cannot crowd out the addendum
#: that names the contract value.
PER_FILE_BUDGET = 2500

#: Files worth reading first. A scope of work decides a Tier 3 bid; a wage
#: determination or a standard terms attachment never does.
_PRIORITY_HINTS = (
    "scope", "sow", "specification", "spec", "statement of work", "solicitation",
    "itb", "rfp", "rfq", "invitation", "addendum", "bid",
)

VALID_DECISIONS = {"PURSUE", "REJECT", "MANUAL_REVIEW"}


# ---------------------------------------------------------------------------
# Reading the attachments
# ---------------------------------------------------------------------------


def _file_priority(path: Path) -> tuple[int, int]:
    """Sort key: scope-bearing documents first, then smallest first.

    Smallest-first within a priority band is deliberate. An addendum naming the
    estimated value is a two-page PDF; the 300-page technical specification next
    to it says nothing about whether the job is subcontractable. Reading the
    small one first spends the budget where the answer is.
    """
    name = path.name.lower()
    rank = 0 if any(hint in name for hint in _PRIORITY_HINTS) else 1
    try:
        size = path.stat().st_size
    except OSError:
        size = 1 << 30
    return rank, size


def read_documents(folder: Path, budget: int = DOCUMENT_BUDGET) -> str:
    """The bid's attachments as text, bounded and ordered by what decides a bid.

    Returns "" when the folder is missing or nothing could be read — the caller
    treats that as "no evidence", not as an error, and the bid stays
    MANUAL_REVIEW rather than being decided on a title.

    Extraction is `sam.engine.text_extractor`'s, which already handles PDF, DOCX
    and TXT and skips anything else. It is portal-agnostic; only its home
    package is SAM's.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return ""

    from app.scrapers.sam.engine.text_extractor import _extract_docx, _extract_pdf, _extract_txt

    readers = {".pdf": _extract_pdf, ".docx": _extract_docx, ".txt": _extract_txt}
    parts: list[str] = []
    used = 0
    for path in sorted((p for p in folder.iterdir() if p.is_file()), key=_file_priority):
        if used >= budget:
            break
        reader = readers.get(path.suffix.lower())
        if reader is None:
            continue
        try:
            text = reader(path) or ""
        except Exception:  # noqa: BLE001 — one unreadable file is not a failed bid
            logger.debug("could not read %s", path.name, exc_info=True)
            continue
        text = " ".join(text.split())
        if not text:
            continue
        slice_ = text[: min(PER_FILE_BUDGET, budget - used)]
        parts.append(f"--- {path.name} ---\n{slice_}")
        used += len(slice_)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------

_TIER3_GUIDANCE = {
    "5.1 construction/trades": """This is a building/facility construction, civil
infrastructure or trades bid. The client has kept some of these and rejected
others that looked identical, so decide it on the document, not the title.
Extract and weigh:
  - Estimated contract value, if the documents state one.
  - Required licences/certifications (e.g. Florida CGC or CUC contractor
    licence, minimum bonding). A bid requiring a Florida trade licence the
    company does not hold leans REJECT.
  - Whether the work is majority on-site skilled-trade labour (leans REJECT) or
    a supply, equipment or subcontractable job (leans PURSUE).""",
    "5.2 sole source": """This is an Agency Decision / sole-source notice. These
name a vendor and are not open for competition. Decide REJECT unless the named
vendor is Rizviz International Impex, in which case say PURSUE. If the notice is
worth keeping only as competitive intelligence, still answer REJECT and say so
in the reason.""",
    "5.3 mixed codes": """This bid carries both an excluded commodity category
and one of the company's own service lanes. Decide which one the actual
deliverable belongs to. If the deliverable is print, graphic design, software,
web, digital marketing, AI/data, electronics or IT staffing, answer PURSUE;
if the excluded category is the real subject and the lane code is incidental,
answer REJECT.""",
    "unmatched": """No deterministic rule covered this bid. Decide whether the
deliverable falls in one of the company's lanes (Software/Web, Printing, Graphic
Design, Digital Marketing, AI/Data, PCB/Electronics, IT staffing/consulting) —
PURSUE if it does, REJECT if it clearly belongs to one of the excluded
categories, MANUAL_REVIEW if the documents genuinely do not say.""",
}

LANES = (
    "Software/Web, Printing, Graphic Design, Digital Marketing, AI/Data, "
    "PCB/Electronics, IT staffing/consulting"
)

EXCLUDED = (
    "Agriculture/forestry/land & wildlife management; health & social-services "
    "grant programs; waste, relocation & roadside services; real estate & "
    "sponsorship; textile care services; generic non-technical program consulting"
)


def build_prompt(record: dict[str, Any], verdict: dict[str, Any], documents: str) -> str:
    """The brief the model answers on.

    Structured rather than a raw dump: the bid's own fields first, then the
    guidance for the specific tier that routed it here, then the document text.
    The tier guidance is what makes the answer usable — a general "is this in
    scope" question on a construction bid reproduces exactly the ambiguity §5.1
    exists to resolve.
    """
    rule = str(verdict.get("rule") or "unmatched")
    guidance = _TIER3_GUIDANCE.get(rule, _TIER3_GUIDANCE["unmatched"])
    evidence = documents or "(no readable attachments — decide on the fields above or answer MANUAL_REVIEW)"

    return f"""You are screening a Florida (MyFloridaMarketPlace) government bid for
Rizviz International Impex.

COMPANY SERVICE LANES (in scope): {LANES}
EXCLUDED CATEGORIES (out of scope): {EXCLUDED}

BID
  Advertisement number : {record.get('ad_number') or '—'}
  Title                : {record.get('title') or '—'}
  Advertisement type   : {record.get('ad_type') or '—'}
  Agency               : {record.get('agency') or '—'}
  Commodity codes      : {record.get('commodity_codes') or '—'}

WHY THE RULE ENGINE COULD NOT DECIDE: {verdict.get('reason') or rule}

WHAT TO DECIDE
{guidance}

ATTACHED DOCUMENT TEXT (truncated):
{evidence}

Respond in EXACTLY this format and nothing else:
DECISION: <PURSUE|REJECT|MANUAL_REVIEW>
REASON: <one sentence, max 20 words, naming the fact that decided it>
CONFIDENCE: <HIGH|MEDIUM|LOW>"""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_ONE_LINE = re.compile(r"\s+")


def parse_response(raw: str) -> dict[str, Any] | None:
    """The model's three lines as a result dict, or None if it did not comply.

    None rather than a guess: an unparseable answer must leave the bid
    MANUAL_REVIEW, because the alternative is tinting a row red on the strength
    of text nobody could read.
    """
    fields: dict[str, str] = {}
    for line in (raw or "").strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields.setdefault(key.strip().upper(), value.strip())

    decision = fields.get("DECISION", "").upper()
    if decision not in VALID_DECISIONS:
        logger.warning("Ollama returned an unusable decision: %r", decision)
        return None

    confidence = fields.get("CONFIDENCE", "LOW").upper()
    if confidence not in {"HIGH", "MEDIUM", "LOW"}:
        confidence = "LOW"

    reason = _ONE_LINE.sub(" ", fields.get("REASON", "")).strip()
    if not reason:
        reason = f"Resolved {decision} by document review"
    # One line, and short enough to read in a spreadsheet cell without widening
    # the column past everything else on the row.
    reason = reason[:200]

    return {
        "decision": decision,
        "ai_notes": reason,
        "confidence": confidence,
        "source": "ollama",
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def resolve(
    record: dict[str, Any],
    verdict: dict[str, Any],
    documents_folder: Path | None = None,
) -> dict[str, Any] | None:
    """Resolve one MANUAL_REVIEW bid against its attachments. Never raises.

    Returns a dict with `decision`, `ai_notes` and `confidence`, or None when
    the bid could not be resolved — disabled, unreachable, timed out, or an
    answer that did not parse. None means the bid stays MANUAL_REVIEW and the
    sheet tints it yellow, which is the honest outcome: nobody decided it.

    A LOW-confidence answer is also refused. The point of this layer is to
    remove bids from the client's manual pass; a coin-flip that lands on REJECT
    hides a bid behind a red fill, which is worse than leaving it yellow.
    """
    if not OLLAMA_ENABLED:
        logger.debug("Ollama disabled — %s stays MANUAL_REVIEW", record.get("ad_number"))
        return None

    documents = read_documents(documents_folder) if documents_folder else ""
    prompt = build_prompt(record, verdict, documents)

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0, "num_predict": 120},
            },
            timeout=OLLAMA_TIMEOUT,
        )
        response.raise_for_status()
        parsed = parse_response(response.json().get("response", ""))
    except requests.Timeout:
        logger.warning("Ollama timed out on %s — staying MANUAL_REVIEW",
                       record.get("ad_number"))
        return None
    except Exception as exc:  # noqa: BLE001 — never propagate; preserve MANUAL_REVIEW
        logger.error("Ollama error on %s: %s", record.get("ad_number"), exc)
        return None

    if parsed is None:
        return None
    if parsed["decision"] != "MANUAL_REVIEW" and parsed["confidence"] == "LOW":
        logger.info(
            "Ollama answered %s with LOW confidence on %s — staying MANUAL_REVIEW",
            parsed["decision"], record.get("ad_number"),
        )
        return {
            "decision": "MANUAL_REVIEW",
            "ai_notes": f"Low confidence: {parsed['ai_notes']}",
            "confidence": "LOW",
            "source": "ollama",
        }
    if not documents:
        parsed["ai_notes"] = f"{parsed['ai_notes']} (no readable attachments)"
    return parsed
