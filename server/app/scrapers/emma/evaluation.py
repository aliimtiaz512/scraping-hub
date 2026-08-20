"""EMMA bid screening — a keyword blocklist over the bid and its documents.

One rule: if any blocked phrase appears in the bid's own text OR in any document
downloaded for it, the bid is REJECTED. Everything else PASSES, and only the
passing bids are reported and exported.

Matching is whole-phrase and word-boundary anchored, so "Construction" does not
fire on "constructive" and "Audit" does not fire on "auditorium". Phrases are
matched with flexible whitespace, so a line break between words in an extracted
PDF still counts.

To change the screen, edit REJECT_KEYWORDS below — nothing else needs touching.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The blocklist. Edit this list to change what gets rejected.
#
# A "/" in a source phrase means alternatives, so those are listed as separate
# entries: "On Call Services / Maintenance Services" becomes "On Call Services"
# and "Maintenance Services"; "Consulting / Engineering Services" becomes
# "Consulting Services" and "Engineering Services".
# ---------------------------------------------------------------------------
REJECT_KEYWORDS: list[str] = [
    "Financial Education",
    "Facilitation Services",
    "Improvement Project",
    "Renovation",
    "Construction",
    "Audit",
    "Transportation Services",
    "Administration Program",
    "Repair and Maintenance",
    "On Call Services",
    "Maintenance Services",
    "Pest Control",
    "Therapy Services",
    "Consulting Services",
    "Engineering Services",
    "Janitorial Services",
    "Custodial Services",
    "Inspection Services",
    "Property Management",
]

# Bid fields whose text is screened alongside the documents.
_TEXT_FIELDS = (
    "title",
    "main_category",
    "solicitation_type",
    "issuing_agency",
    "award_status",
    "bpm_code",
)


def _compile(phrase: str) -> re.Pattern[str]:
    """A whole-phrase, word-boundary matcher tolerant of any whitespace run.

    The final word matches singular or plural, so "Renovation" also catches
    "Renovations" and "Janitorial Services" also catches "Janitorial Service".
    The word boundaries still hold, so "Audit" never fires on "auditorium" and
    "Construction" never fires on "constructive".
    """
    words = [re.escape(w) for w in phrase.split()]
    if words:
        last = words[-1]
        if last.lower().endswith("s"):
            last = last[:-1]  # store the stem so the trailing "s" is optional
        words[-1] = last + "s?"
    return re.compile(r"\b" + r"\s+".join(words) + r"\b", re.IGNORECASE)


_PATTERNS: list[tuple[str, re.Pattern[str]]] = [(k, _compile(k)) for k in REJECT_KEYWORDS]


def bid_text(record: dict[str, Any]) -> str:
    """The bid's own text: its grid fields plus every detail-page field value."""
    parts = [str(record.get(f) or "") for f in _TEXT_FIELDS]
    parts.extend(str(v or "") for v in (record.get("detail") or {}).values())
    return "\n".join(p for p in parts if p)


def document_text(folder: Path | str | None) -> str:
    """Text of every PDF / DOCX / TXT downloaded for one bid.

    Delegates to the SAM engine's extractor (read-only reuse). Never raises: a
    document we cannot read simply contributes no text, and the bid is then
    screened on whatever else is available.
    """
    if not folder:
        return ""
    path = Path(folder)
    try:
        from app.scrapers.sam.engine.text_extractor import extract_text_from_folder

        return extract_text_from_folder(path)
    except Exception:  # noqa: BLE001 — screening must survive an unreadable file
        logger.exception("could not extract document text from %s", path)
        return ""


def find_match(text: str) -> str | None:
    """The first blocked phrase present in `text`, or None."""
    if not text:
        return None
    for keyword, pattern in _PATTERNS:
        if pattern.search(text):
            return keyword
    return None


def evaluate(record: dict[str, Any], doc_text: str = "") -> dict[str, Any]:
    """Screen one bid against the blocklist.

    Returns ``{"decision": "PASS"|"REJECT", "matched_keyword", "matched_in"}``.
    The bid's own text is checked first so a hit there is reported as such;
    otherwise the documents are checked.
    """
    hit = find_match(bid_text(record))
    if hit:
        return {"decision": "REJECT", "matched_keyword": hit, "matched_in": "bid"}

    hit = find_match(doc_text)
    if hit:
        return {"decision": "REJECT", "matched_keyword": hit, "matched_in": "documents"}

    return {"decision": "PASS", "matched_keyword": "", "matched_in": ""}
