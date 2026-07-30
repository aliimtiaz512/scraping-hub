"""C / T / S scoring — criteria doc §3.

`niche_score = C + T + S`, capped at 100. Pure functions: every weight, cap and
band comes from the Config, never from a literal here (§9.2). A number written
into this module would be a bug, not a style choice.

Evaluation order is T and S first, then C. That is forced by §3.1's fourth row
— a code that is Tier A for a *different* niche scores 10 for this one only
"[when] this niche's title/scope signals fire" — so C cannot be computed until
T and S are known.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.scrapers.myflorida.sweep.config import TIER_A, TIER_B, TIER_C, Config, Niche
from app.scrapers.myflorida.sweep.matching import MatchResult, find_terms


@dataclass
class CodeHit:
    code: str
    tier: str          # tier_a | tier_b | tier_c | prefix6 | other_niche | unrelated | absent
    points: int


@dataclass
class NicheScore:
    """One niche's verdict on one advertisement."""

    niche: str
    code: int = 0
    title: int = 0
    scope: int = 0
    code_hits: list[CodeHit] = field(default_factory=list)
    title_matched: list[str] = field(default_factory=list)
    title_suppressed: list[str] = field(default_factory=list)
    scope_matched: list[str] = field(default_factory=list)
    scope_suppressed: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    deep_read_required: bool = False

    @property
    def total(self) -> int:
        return self.code + self.title + self.scope

    @property
    def matched_keywords(self) -> list[str]:
        """Every surviving term, tagged with the field it fired in (§9.5)."""
        return [f"title:{t}" for t in self.title_matched] + [
            f"scope:{t}" for t in self.scope_matched
        ]

    @property
    def suppressed_terms(self) -> list[str]:
        return [f"title:{t}" for t in self.title_suppressed] + [
            f"scope:{t}" for t in self.scope_suppressed
        ]


# -- T: title signal (§3.2, 0-25) --------------------------------------------

def score_title(config: Config, niche: Niche, title: str) -> tuple[int, MatchResult, bool]:
    """Returns (points, core-term matches, deep_read_required).

    An umbrella-only hit scores the umbrella value and flags the ad for a deep
    read rather than being treated as a real core match.
    """
    weights = config.scoring["title"]
    core = find_terms(title, niche.core_terms, niche.exclusion_terms, niche.stem_map)

    if not core.matched:
        umbrella = find_terms(title, niche.umbrella_terms, niche.exclusion_terms, niche.stem_map)
        if umbrella.matched:
            return int(weights["umbrella_only"]), umbrella, True
        return 0, core, False

    points = int(weights["core_first"])
    if core.count >= 2:
        points += int(weights["core_additional"])
    if any_modifier(config, title):
        points += int(weights["high_intent_modifier"])
    return min(points, int(weights["max"])), core, False


def any_modifier(config: Config, text: str) -> bool:
    from app.scrapers.myflorida.sweep.matching import any_term

    return any_term(text, config.high_intent_modifiers)


# -- S: scope signal (§3.3, 0-30) --------------------------------------------

def score_scope(
    config: Config, niche: Niche, scope: str
) -> tuple[int, MatchResult, list[str]]:
    """Returns (points, core+supporting matches, deliverables named).

    The base comes from the count of *distinct* niche terms; naming an artefact
    from the niche's `deliverables` list adds the bonus, because an artefact is
    the clearest niche signal a scope can give.
    """
    weights = config.scoring["scope"]
    table: list[int] = list(weights["base_by_distinct"])

    matches = find_terms(
        scope,
        niche.core_terms + niche.supporting_terms,
        niche.exclusion_terms,
        niche.stem_map,
    )
    points = table[min(matches.count, len(table) - 1)]

    deliverables = find_terms(scope, niche.deliverables, niche.exclusion_terms, niche.stem_map)
    if deliverables.matched:
        points += int(weights["deliverable_bonus"])

    return min(points, int(weights["max"])), matches, deliverables.matched


# -- C: commodity-code alignment (§3.1, 0-45) --------------------------------

def score_codes(
    config: Config, niche: Niche, codes: list[str], text_fired: bool
) -> tuple[int, list[CodeHit]]:
    """Best-scoring published code for this niche.

    `text_fired` is whether this niche's title or scope produced anything — it
    gates the "Tier A for a different niche" row, which is worth 10 only when
    the niche has independent text evidence.

    No codes published at all is the neutral case, not the zero case: §3.1 calls
    that rule load-bearing, because scoring an uncoded ad at zero would push real
    opportunities into Other on a technicality.
    """
    weights = config.scoring["code"]

    if not codes:
        return int(weights["absent"]), [CodeHit("", "absent", int(weights["absent"]))]

    tier_points = {
        TIER_A: int(weights["tier_a"]),
        TIER_B: int(weights["tier_b"]),
        TIER_C: int(weights["tier_c"]),
    }
    prefixes = {code[:6] for code in niche.tier_a_codes()}
    hits: list[CodeHit] = []

    for code in codes:
        tier = niche.tier_of(code)
        if tier:
            hits.append(CodeHit(code, tier, tier_points[tier]))
        elif len(code) >= 6 and code[:6] in prefixes:
            hits.append(CodeHit(code, "prefix6", int(weights["prefix6_of_tier_a"])))
        elif text_fired and config.niches_owning_tier_a(code):
            hits.append(CodeHit(code, "other_niche", int(weights["other_niche_tier_a"])))
        else:
            hits.append(CodeHit(code, "unrelated", int(weights["unrelated"])))

    best = max(hits, key=lambda h: h.points)
    return min(best.points, int(weights["max"])), hits


# -- the whole score ---------------------------------------------------------

def score_niche(
    config: Config, niche: Niche, title: str, scope: str, codes: list[str]
) -> NicheScore:
    """Score one advertisement against one niche."""
    title_points, title_match, deep_read = score_title(config, niche, title)
    scope_points, scope_match, deliverables = score_scope(config, niche, scope)
    code_points, code_hits = score_codes(
        config, niche, codes, text_fired=bool(title_points or scope_points)
    )

    return NicheScore(
        niche=niche.key,
        code=code_points,
        title=title_points,
        scope=scope_points,
        code_hits=code_hits,
        title_matched=title_match.matched,
        title_suppressed=title_match.suppressed,
        scope_matched=scope_match.matched,
        scope_suppressed=scope_match.suppressed,
        deliverables=deliverables,
        deep_read_required=deep_read,
    )


def score_all(
    config: Config, title: str, scope: str, codes: list[str]
) -> dict[str, NicheScore]:
    """Score against **every** niche — §9.3 forbids an early exit on first match,
    which is what stops everything piling into one lane."""
    return {
        niche.key: score_niche(config, niche, title, scope, codes)
        for niche in config.ordered_niches()
    }


def match_strength(config: Config, total: int) -> str | None:
    """STRONG / PROBABLE / POSSIBLE, or None below the match threshold (§4)."""
    bands = config.thresholds["strength"]
    if total >= int(bands["strong"]):
        return "STRONG"
    if total >= int(bands["probable"]):
        return "PROBABLE"
    if total >= int(bands["possible"]):
        return "POSSIBLE"
    return None
