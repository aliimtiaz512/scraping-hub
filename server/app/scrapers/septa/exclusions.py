"""Summary blacklist for SEPTA Open Quotes.

A quote whose summary names one of these is skipped outright — not evaluated,
not stored, not exported. SEPTA's Open Quotes grid is a parts-requisition feed
(`MODULE CUMMINS 5579356RX PARTICULATE`, `BRACKET NEW FLYER 695953
ASSY-ORBSTAR`), and these terms mark the parts that are out of scope: the bus
and engine manufacturers, plus gaskets whoever makes them.

Every term stands alone — none of them is a phrase requiring two adjacent
words. `NEW FLYER` is two words because the manufacturer's name is.

Matching is **whole-word, case-insensitive** — never a plain substring, which
is the whole reason this lives in its own module with its own tests.

One consequence of whole-word matching worth knowing: a plural would not match
(`GASKETS` is not `GASKET`). SEPTA's parts nomenclature is consistently
singular — no plural or possessive form of any of these terms appears anywhere
in the scraped corpus — so this is a stated limit rather than a live gap.

Two of the terms are short enough that substring matching quietly destroys good
data. Measured against 47 real scraped summaries:

    'NF'   substring=7  word-boundary=6
      [WORD ]       FILTER NF 6321254 AIR
      [WORD ]       LATCH NF 8110774 RH
      [WORD ]       RIVET USSC 9904-000018-005 (NF 6406513)
      [SUBSTR-ONLY] IGBT INFINEON BSM400GA170DLC NO      <- wrongly excluded

`INFI**NF**INEON` is a legitimate quote, and that is one false positive in a
sample of forty-seven. At full-catalogue scale the same bug also eats
`CO(NF)IGURATION`, `TRA(NF)ER`, `MA(NF)OLD`, and `IN(NOVA)TION` /
`IN(NOVA)TIVE`. Word boundaries spare all of them while still catching the
parenthesised `(NF 6406513)`, because `(` is not a word character.

Phrases tolerate any run of whitespace between their words, so a summary that
wraps or double-spaces (`NEW  FLYER`) is still matched.
"""

from __future__ import annotations

import re

# Exact terms to exclude. Case-insensitive; whole-word.
#
# GASKET and CUMMINS are **independent terms, not the phrase "GASKET CUMMINS"**.
# As a phrase it only fired when the two words were adjacent, so one row was
# dropped and seven Cummins parts came through untouched:
#
#     GASKET CUMMINS 3974127 FILTER HEAD      <- dropped
#     HEAD CUMMINS 3955034 LUBE OIL FILTER    <- kept
#     MODULE CUMMINS 5579356RX PARTICULATE    <- kept
#     KIT CUMMINS 3977913 GASKET, LUBE OIL    <- kept (both words, not adjacent)
#
# Separately they drop every Cummins part and every gasket, whoever makes it —
# `GASKET MEULLER INDUSTRIES P35708` goes too, which is intended.
#
# Order is preserved so the reported reason is stable when a summary contains
# more than one, and follows the order the terms were originally given.
EXCLUDED_SUMMARY_TERMS: tuple[str, ...] = (
    "GASKET",
    "CUMMINS",
    "NF",
    "NOVA",
    "NEW FLYER",
)


def _compile(term: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive, whitespace-flexible matcher for `term`.

    `(?<!\\w)` / `(?!\\w)` rather than `\\b`: they behave identically for these
    terms but stay correct if a term is ever added that starts or ends with a
    non-word character, where `\\b` silently inverts its meaning.
    """
    body = r"\s+".join(re.escape(word) for word in term.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (term, _compile(term)) for term in EXCLUDED_SUMMARY_TERMS
)


def excluded_by(summary: str | None) -> str | None:
    """The blacklisted term appearing in `summary`, or None to keep the quote.

    Returns the term itself rather than a bool so the caller can log and tally
    *why* a quote was dropped — a filter that removes rows without saying which
    rule fired is indistinguishable from a scrape that missed them.
    """
    if not summary:
        return None
    for term, pattern in _PATTERNS:
        if pattern.search(summary):
            return term
    return None


def is_excluded(summary: str | None) -> bool:
    """True when `summary` names a blacklisted term."""
    return excluded_by(summary) is not None
