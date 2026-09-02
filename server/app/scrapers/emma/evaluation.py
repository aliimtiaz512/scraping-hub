"""EMMA bid screening — a keyword blocklist over the bid and its documents.

One rule: if any blocked phrase appears in the bid's own text OR in any document
downloaded for it, the bid is REJECTED. Everything else PASSES, and only the
passing bids are reported and exported.

Three lists, three reasons, recorded separately so a run can say which fired:

    REJECT_KEYWORDS        work that is out of scope for us
    MASTER_CONTRACT_PHRASES  open only to existing master-contract holders
    NOT_BIDDABLE_PHRASES   not a solicitation at all (Requests for Information)

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

# Master-contract solicitations are skipped too: they are open only to existing
# master-contract holders, so they are not biddable for us. EMMA has no
# free-text summary field, so these are matched over the same text as the
# blocklist above — the bid's own fields and its documents. Singular/plural is
# handled automatically, so "Master Contract" also catches "Master Contracts"
# and "Master Contractor" also catches "Master Contractors".
MASTER_CONTRACT_PHRASES: list[str] = [
    "Only Master Contracts",
    "Master Contract",
    "Master Contractor",
    "Master Contract Holder",
]

# Requests for Information are discarded too, and for a different reason again:
# an RFI is not a solicitation at all. It asks the market to describe what it can
# supply so the agency can write a real bid later — there is nothing to price and
# nothing to win, so a run that reported them would be padding its own count.
#
# Its own list rather than another entry in REJECT_KEYWORDS, because the three
# lists mean three different things and the run log says which fired: "out of
# scope work", "open only to master-contract holders", and "not biddable at all".
# The same split exists in the SAM engine, where `rfi` and `sources sought` are
# kill-words checked ahead of the Rule B/C scope lists rather than inside them.
#
# "RFI" is included as its own phrase. Matching is word-boundary anchored, so it
# fires on the standalone token and not inside another word — and in procurement
# text a bare "RFI" is a Request for Information essentially without exception.
# Drop it from this list if a real bid is ever screened out by it.
NOT_BIDDABLE_PHRASES: list[str] = [
    "Request for Information",
    # The plural is spelled out because `_compile` only makes the *last* word's
    # "s" optional — that covers "Renovation(s)" and "Janitorial Service(s)", but
    # this is the one phrase in the file whose plural falls on the head noun.
    "Requests for Information",
    "RFI",
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


# (phrase, matcher, why) — "why" distinguishes a blocklist hit from a
# master-contract skip in the run log and the stored record.
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = (
    [(k, _compile(k), "keyword") for k in REJECT_KEYWORDS]
    + [(k, _compile(k), "master_contract") for k in MASTER_CONTRACT_PHRASES]
    + [(k, _compile(k), "not_biddable") for k in NOT_BIDDABLE_PHRASES]
)


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


def find_match(text: str) -> tuple[str, str] | None:
    """The first blocked/skip phrase in `text` as (phrase, why), or None."""
    if not text:
        return None
    for phrase, pattern, why in _PATTERNS:
        if pattern.search(text):
            return phrase, why
    return None


def evaluate(record: dict[str, Any], doc_text: str = "") -> dict[str, Any]:
    """Screen one bid against the blocklist and the master-contract skip list.

    Returns ``{"decision": "PASS"|"REJECT", "matched_keyword", "matched_in",
    "matched_rule"}``. The bid's own text is checked first so a hit there is
    reported as such; otherwise the documents are checked.
    """
    for source, text in (("bid", bid_text(record)), ("documents", doc_text)):
        hit = find_match(text)
        if hit:
            phrase, why = hit
            return {
                "decision": "REJECT",
                "matched_keyword": phrase,
                "matched_in": source,
                "matched_rule": why,
            }
    return {"decision": "PASS", "matched_keyword": "", "matched_in": "", "matched_rule": ""}
