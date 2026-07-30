"""Term matching with exclusion suppression — criteria doc §2.1 and §3.2.

Matching is case-insensitive, whole-word and phrase-aware, with a niche's
`stem_map` expanding each term to its declared variants.

Exclusion terms are **suppressors, not penalties**: they never subtract points.
When a core or supporting term occurs *inside* an exclusion phrase, that
occurrence does not count — "3D printing" must not register as a `printing`
hit for N3, and "landscape design" must not register as a `design` hit for N1.
A term whose every occurrence is suppressed scores nothing, and is reported in
`suppressed` so a wrong classification can be debugged from the workbook alone
(§9.5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Collapse whitespace so a phrase split across a line break still matches.
_WHITESPACE = re.compile(r"\s+")


@dataclass
class MatchResult:
    """Which terms fired in one field, and which were cancelled."""

    matched: list[str] = field(default_factory=list)
    suppressed: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        """Distinct canonical terms that survived suppression."""
        return len(self.matched)

    def __bool__(self) -> bool:
        return bool(self.matched)


def normalize(text: str | None) -> str:
    """Lowercase and collapse whitespace. Cheap, and applied to every field."""
    return _WHITESPACE.sub(" ", (text or "").lower()).strip()


def _pattern(term: str) -> re.Pattern[str]:
    r"""Whole-word, phrase-aware matcher for one term.

    `\b` is wrong at a non-word boundary: a term like "ui/ux" ends in "x" but
    starts with "u", and one like "c++" would break the guard entirely. Guarding
    with lookarounds for word characters only where the term itself starts or
    ends with one keeps both cases correct.
    """
    escaped = re.escape(term).replace(r"\ ", r"\s+")
    left = r"(?<![a-z0-9])" if term[:1].isalnum() else ""
    right = r"(?![a-z0-9])" if term[-1:].isalnum() else ""
    return re.compile(f"{left}{escaped}{right}")


def _variants(term: str, stem_map: dict[str, list[str]]) -> list[str]:
    """A term plus any declared stem variants, longest first.

    Longest-first matters for span containment: matching "printing" before
    "print" means the recorded span covers the longer surface form, which is
    what the exclusion check compares against.
    """
    out = {term, *stem_map.get(term, [])}
    for base, forms in stem_map.items():
        if base in term:
            out.update(term.replace(base, form) for form in forms)
    return sorted(out, key=len, reverse=True)


def _spans(text: str, terms: list[str]) -> list[tuple[int, int]]:
    """Character spans of every occurrence of any of `terms` in `text`."""
    spans: list[tuple[int, int]] = []
    for term in terms:
        if not term:
            continue
        spans.extend((m.start(), m.end()) for m in _pattern(term).finditer(text))
    return spans


def find_terms(
    text: str,
    terms: list[str],
    exclusion_terms: list[str] | None = None,
    stem_map: dict[str, list[str]] | None = None,
) -> MatchResult:
    """Distinct terms from `terms` present in `text`, minus suppressed hits.

    A hit is suppressed when its span falls entirely inside the span of an
    exclusion phrase. Returned terms are the canonical forms as declared in the
    YAML, not the surface form that matched, so downstream counting is stable.
    """
    haystack = normalize(text)
    if not haystack:
        return MatchResult()

    excluded = _spans(haystack, exclusion_terms or [])
    stems = stem_map or {}
    result = MatchResult()

    for term in terms:
        hits = _spans(haystack, _variants(term, stems))
        if not hits:
            continue
        survives = any(
            not any(lo <= start and end <= hi for lo, hi in excluded) for start, end in hits
        )
        (result.matched if survives else result.suppressed).append(term)

    return result


def any_term(text: str, terms: list[str]) -> bool:
    """True if any of `terms` occurs in `text`. No suppression — used for the
    N6 hard override (§5.1) and tie-break side detection, neither of which the
    criteria doc subjects to exclusion terms."""
    haystack = normalize(text)
    return bool(haystack) and bool(_spans(haystack, terms))


def count_terms(text: str, terms: list[str]) -> int:
    """How many distinct terms from `terms` occur in `text`. Tie-break sides."""
    haystack = normalize(text)
    if not haystack:
        return 0
    return sum(1 for term in terms if term and _pattern(term).search(haystack))
