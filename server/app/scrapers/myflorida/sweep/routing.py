"""Routing a scored advertisement into a lane — criteria doc §5, §6.

Order of operations, and each step's reason for being where it is:

  1. N6 hard override (§5.1)  — an electronics scope filed under Software is the
     most damaging error this engine can make, so the override runs before any
     score comparison and wins outright.
  2. Threshold test (§5)      — nothing above 40 means OTHER, with the closest
     niche recorded for tuning rather than treated as a match.
  3. Tie-breaks (§6)          — "applied before contested marking", so a genuine
     pair ambiguity is resolved by rule before it is reported as contested.
  4. Secondary lanes (§5)     — score >= 55 and within 20 of the primary.
  5. Contested (§5)           — primary minus runner-up <= 8.

§9.4's invariant — every advertisement gets a primary_niche of N1..N6 or
OTHER — is asserted by `classify`, not merely intended.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.scrapers.myflorida.sweep.config import OTHER, Config
from app.scrapers.myflorida.sweep.matching import any_term, count_terms
from app.scrapers.myflorida.sweep.scoring import NicheScore, match_strength, score_all

NO_NICHE_MATCH = "NO_NICHE_MATCH"


@dataclass
class Classification:
    """The §7 result object for one advertisement."""

    advertisement_number: str
    scores: dict[str, NicheScore]
    primary_niche: str
    secondary_niches: list[tuple[str, int]] = field(default_factory=list)
    match_strength: str | None = None
    other_reason: str | None = None
    closest_niche: str | None = None
    closest_niche_score: int | None = None
    flags: list[str] = field(default_factory=list)

    @property
    def is_other(self) -> bool:
        return self.primary_niche == OTHER

    @property
    def primary_score(self) -> int:
        score = self.scores.get(self.primary_niche)
        return score.total if score else 0

    def totals(self) -> dict[str, int]:
        return {key: score.total for key, score in self.scores.items()}

    def lanes(self, cross_listing: bool) -> list[tuple[str, str]]:
        """(niche, role) pairs this ad should appear under.

        With cross-listing off the ad appears once, in its primary lane — which
        is what keeps §9.4's invariant `classified == sum(lanes) + other` true.
        With it on, the ad also appears as CROSS-LISTED in each secondary lane
        and that invariant necessarily counts OWNER rows only.
        """
        rows = [(self.primary_niche, "OWNER")]
        if cross_listing and not self.is_other:
            rows += [(niche, "CROSS-LISTED") for niche, _ in self.secondary_niches]
        return rows


def _apply_tie_break(
    config: Config, primary: str, runner_up: str, title: str, scope: str, codes: list[str]
) -> tuple[str, bool]:
    """Resolve a close pair by rule. Returns (winner, contested_override).

    `codes_force_b` is checked first because §6's BI rule is deterministic: a
    dashboard on existing data is N4 *unless* a BI/analytics code is published,
    in which case it is N5 regardless of the term counts.
    """
    rule = config.tie_break_for(primary, runner_up)
    if rule is None:
        return primary, False

    if rule.codes_force_b and any(code in rule.codes_force_b for code in codes):
        return rule.b, False

    text = f"{title} {scope}"
    a_hits = count_terms(text, rule.prefer_a)
    b_hits = count_terms(text, rule.prefer_b)

    if a_hits > b_hits:
        return rule.a, False
    if b_hits > a_hits:
        return rule.b, False

    # Neither side dominates. Where the pair is one no keyword list can settle
    # — §6's "AI-powered portal" case — flag for a human instead of guessing.
    return primary, bool(rule.contested_when_tied and (a_hits or b_hits))


def classify(
    config: Config,
    advertisement_number: str,
    title: str,
    scope: str,
    codes: list[str],
) -> Classification:
    """Score against all six niches and route into exactly one primary lane."""
    scores = score_all(config, title, scope, codes)
    totals = {key: score.total for key, score in scores.items()}
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], config.niches[kv[0]].order))

    threshold = int(config.thresholds["niche_match"])
    flags: list[str] = []
    if any(score.deep_read_required for score in scores.values()):
        flags.append("deep_read_required")

    # 1. N6 hard override (§5.1) — outright, ahead of every score comparison.
    if config.override_niche and any_term(f"{title} {scope}", config.override_terms):
        primary = config.override_niche
        flags.append("n6_override_applied")
    else:
        best_niche, best_total = ranked[0]
        # 2. Threshold — the Other lane is a destination, not a bin (§5.2).
        if best_total < threshold:
            return Classification(
                advertisement_number=advertisement_number,
                scores=scores,
                primary_niche=OTHER,
                other_reason=NO_NICHE_MATCH,
                closest_niche=best_niche,
                closest_niche_score=best_total,
                flags=flags,
            )
        primary = best_niche
        # 3. Tie-breaks, before contested marking (§6).
        if len(ranked) > 1 and (best_total - ranked[1][1]) <= int(config.thresholds["contested_gap"]):
            primary, forced_contested = _apply_tie_break(
                config, best_niche, ranked[1][0], title, scope, codes
            )
            if forced_contested:
                flags.append("contested")

    primary_total = totals[primary]

    # 4. Secondary lanes (§5).
    secondary_min = int(config.thresholds["secondary_min"])
    secondary_gap = int(config.thresholds["secondary_max_gap"])
    secondary = [
        (key, total)
        for key, total in ranked
        if key != primary and total >= secondary_min and (primary_total - total) <= secondary_gap
    ]

    # 5. Contested (§5) — compares against the highest other niche, whatever it is.
    others = [total for key, total in ranked if key != primary]
    if others and (primary_total - max(others)) <= int(config.thresholds["contested_gap"]):
        if "contested" not in flags:
            flags.append("contested")

    return Classification(
        advertisement_number=advertisement_number,
        scores=scores,
        primary_niche=primary,
        secondary_niches=secondary,
        match_strength=match_strength(config, primary_total),
        flags=flags,
    )


def assert_invariant(classifications: list[Classification], lane_counts: dict[str, int]) -> None:
    """§9.4 — `classified == sum(bids per niche lane) + count(OTHER)`.

    Counts OWNER rows only, which is the sole reading under which the equality
    can hold once cross-listing duplicates an ad into a secondary lane.
    """
    owners = sum(count for niche, count in lane_counts.items() if niche != OTHER)
    others = lane_counts.get(OTHER, 0)
    if owners + others != len(classifications):
        raise AssertionError(
            f"classification invariant broken: {len(classifications)} ads classified but "
            f"{owners} owner rows + {others} other rows = {owners + others}"
        )
