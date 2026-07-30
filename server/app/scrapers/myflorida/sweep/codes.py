"""Commodity-code extraction — the classifier's single largest signal.

Criteria doc §3.1 puts 45 of 100 points on commodity-code alignment, so where
the codes come from matters more than any other input. Two sources, tried in
order, with the winner recorded so a systematic failure is visible in the
workbook rather than silent:

  export   the portal's own Excel export column, when it has one. The mapping
           already exists in ingest.FIELD_CANDIDATES ("commoditycode",
           "unspsc", "nigp", …) and is reused rather than re-guessed.
  description
           an 8-digit scan of the `#mainSection` body, which lists them under a
           "Commodities:" heading with their titles alongside.

If neither yields anything the ad is uncoded, which §3.1 treats as neutral (15)
rather than zero — see the plan's §4.1 for why that makes scope text
load-bearing for recall.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

SOURCE_EXPORT = "export"
SOURCE_DESCRIPTION = "description"
SOURCE_NONE = "none"

# UNSPSC codes are exactly 8 digits. Bounded so a 10-digit amount or a phone
# number can't be mistaken for one.
_CODE = re.compile(r"(?<!\d)(\d{8})(?!\d)")

# Codes in the description sit under this heading; used only to prefer that
# region when it exists, never to require it.
_COMMODITIES_HEADING = re.compile(r"commodit(?:y|ies)\s*:?", re.IGNORECASE)


def from_export_value(value: str | None) -> list[str]:
    """Codes out of one export cell — comma, semicolon, newline or space separated."""
    if not value:
        return []
    return _dedupe(_CODE.findall(str(value)))


def from_description(description: str | None) -> list[str]:
    """8-digit codes in the ad body.

    When a "Commodities:" heading is present the scan starts there, which avoids
    picking up an 8-digit figure that happens to appear earlier in the prose. If
    there is no heading the whole body is scanned — a code anywhere is better
    evidence than none.
    """
    if not description:
        return []
    match = _COMMODITIES_HEADING.search(description)
    region = description[match.end():] if match else description
    found = _dedupe(_CODE.findall(region))
    # A heading that yielded nothing is not proof of absence — the layout may
    # differ. Fall back to the whole body before giving up.
    if not found and match:
        found = _dedupe(_CODE.findall(description))
    return found


def extract(export_value: str | None, description: str | None) -> tuple[list[str], str]:
    """Return (codes, source). The export wins when it has anything at all."""
    codes = from_export_value(export_value)
    if codes:
        return codes, SOURCE_EXPORT
    codes = from_description(description)
    if codes:
        return codes, SOURCE_DESCRIPTION
    return [], SOURCE_NONE


def _dedupe(values: list[str]) -> list[str]:
    """Preserve first-seen order; the best-scoring code is chosen later anyway."""
    return list(dict.fromkeys(values))
