"""Turning classifications into rows, into the DB, and back into a workbook.

`generate_excel(run_id, path)` is the contract `app.core.exports` calls to
rebuild a run's sheet on demand (download button, completion email), so the
workbook a user gets months later is regenerated from the DB rather than read
off a disk that no longer has it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.scrapers.myflorida.sweep.config import OTHER, Config, get_config
from app.scrapers.myflorida.sweep.models import SweepBid, SweepScore
from app.scrapers.myflorida.sweep.routing import Classification
from app.scrapers.myflorida.sweep.workbook import build_workbook

logger = logging.getLogger(__name__)

NICHE_COLUMN_KEYS = ["n1", "n2", "n3", "n4", "n5", "n6"]


def _code_labels(classification: Classification, niche: str) -> list[str]:
    """"82121500 (tier_a)" for each published code, as scored for this niche."""
    score = classification.scores.get(niche)
    if not score:
        return []
    return [f"{hit.code} ({hit.tier})" for hit in score.code_hits if hit.code]


def to_rows(
    config: Config, classification: Classification, ad: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    """(lane, row) pairs for one advertisement.

    One pair with cross-listing off; with it on, the primary lane's OWNER row
    plus a CROSS-LISTED row in each secondary lane. `ad` carries the portal
    fields and the document/description provenance.
    """
    totals = classification.totals()
    ordered = config.ordered_niches()
    niche_columns = {
        key: totals.get(niche.key, 0)
        for key, niche in zip(NICHE_COLUMN_KEYS, ordered)
    }

    out: list[tuple[str, dict[str, Any]]] = []
    for lane, role in classification.lanes(config.cross_listing):
        lane_score = classification.scores.get(lane)
        row: dict[str, Any] = {
            **{k: ad.get(k) for k in (
                "ad_number", "title", "agency", "ad_type", "status",
                "ad_date", "open_date", "close_date", "description",
            )},
            "role": role,
            "match_strength": classification.match_strength,
            "score": totals.get(lane, 0) if lane != OTHER else classification.primary_score,
            "code_points": lane_score.code if lane_score else "",
            "title_points": lane_score.title if lane_score else "",
            "scope_points": lane_score.scope if lane_score else "",
            **niche_columns,
            "primary_niche": classification.primary_niche,
            "secondary_niches": [f"{n} ({s})" for n, s in classification.secondary_niches],
            "matched_codes": _code_labels(classification, lane if lane != OTHER else
                                          (classification.closest_niche or "")),
            "code_source": ad.get("code_source"),
            "matched_keywords": lane_score.matched_keywords if lane_score else [],
            "deliverables_detected": lane_score.deliverables if lane_score else [],
            "suppressed_terms": lane_score.suppressed_terms if lane_score else [],
            "flags": classification.flags,
            "documents": ad.get("documents") or [],
            "document_chars": ad.get("document_chars", 0),
        }
        if lane == OTHER:
            closest = classification.closest_niche
            closest_score = classification.scores.get(closest or "")
            row.update(
                other_reason=classification.other_reason,
                closest_niche=closest,
                closest_niche_score=classification.closest_niche_score,
                # For an Other row the only meaningful breakdown is the near-miss.
                code_points=closest_score.code if closest_score else "",
                title_points=closest_score.title if closest_score else "",
                scope_points=closest_score.scope if closest_score else "",
                score=classification.closest_niche_score,
                matched_keywords=closest_score.matched_keywords if closest_score else [],
                suppressed_terms=closest_score.suppressed_terms if closest_score else [],
            )
        out.append((lane, row))
    return out


def rows_by_lane(
    config: Config, results: list[tuple[Classification, dict[str, Any]]]
) -> dict[str, list[dict[str, Any]]]:
    lanes: dict[str, list[dict[str, Any]]] = {n.key: [] for n in config.ordered_niches()}
    lanes[OTHER] = []
    for classification, ad in results:
        for lane, row in to_rows(config, classification, ad):
            lanes.setdefault(lane, []).append(row)
    return lanes


# -- persistence -------------------------------------------------------------

def save(run_id: str, results: list[tuple[Classification, dict[str, Any]]]) -> int:
    """Store bids and their six scores in one transaction. Returns rows stored."""
    session = SessionLocal()
    stored = 0
    try:
        for classification, ad in results:
            bid = SweepBid(
                run_id=run_id,
                ad_number=classification.advertisement_number,
                title=ad.get("title"),
                agency=ad.get("agency"),
                ad_type=ad.get("ad_type"),
                status=ad.get("status"),
                ad_date=ad.get("ad_date"),
                open_date=ad.get("open_date"),
                close_date=ad.get("close_date"),
                description=ad.get("description"),
                primary_niche=classification.primary_niche,
                primary_score=classification.primary_score,
                match_strength=classification.match_strength,
                secondary_niches=[[n, s] for n, s in classification.secondary_niches],
                other_reason=classification.other_reason,
                closest_niche=classification.closest_niche,
                closest_niche_score=classification.closest_niche_score,
                flags=classification.flags,
                matched_codes=_code_labels(classification, classification.primary_niche),
                code_source=ad.get("code_source"),
                matched_keywords={
                    key: score.matched_keywords for key, score in classification.scores.items()
                },
                suppressed_terms={
                    key: score.suppressed_terms for key, score in classification.scores.items()
                },
                deliverables_detected={
                    key: score.deliverables for key, score in classification.scores.items()
                },
                documents=ad.get("documents") or [],
                document_chars=ad.get("document_chars", 0),
                raw_data=ad.get("raw_data"),
            )
            session.add(bid)
            session.flush()  # need bid.id for the score rows
            for key, score in classification.scores.items():
                session.add(SweepScore(
                    bid_id=bid.id,
                    run_id=run_id,
                    ad_number=classification.advertisement_number,
                    niche=key,
                    total=score.total,
                    code_points=score.code,
                    title_points=score.title,
                    scope_points=score.scope,
                    matched_keywords=score.matched_keywords,
                    suppressed_terms=score.suppressed_terms,
                ))
            stored += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return stored


def generate_excel(run_id: str, out_path: str | Path) -> int:
    """Rebuild the run's workbook from the DB. Returns the advertisement count.

    This is the hook `app.core.exports._GENERATOR_PORTALS` calls, so a download
    months later is regenerated rather than read from a workspace long deleted.
    """
    config = get_config()
    session = SessionLocal()
    try:
        bids = session.execute(
            select(SweepBid).where(SweepBid.run_id == run_id).order_by(SweepBid.id)
        ).scalars().all()
        scores = session.execute(
            select(SweepScore).where(SweepScore.run_id == run_id)
        ).scalars().all()
    finally:
        session.close()

    by_ad: dict[str, dict[str, SweepScore]] = {}
    for score in scores:
        by_ad.setdefault(score.ad_number, {})[score.niche] = score

    lanes: dict[str, list[dict[str, Any]]] = {n.key: [] for n in config.ordered_niches()}
    lanes[OTHER] = []
    ordered = config.ordered_niches()

    for bid in bids:
        ad_scores = by_ad.get(bid.ad_number, {})
        niche_columns = {
            key: (ad_scores[niche.key].total if niche.key in ad_scores else 0)
            for key, niche in zip(NICHE_COLUMN_KEYS, ordered)
        }
        lane = bid.primary_niche
        own = ad_scores.get(lane) or ad_scores.get(bid.closest_niche or "")
        row: dict[str, Any] = {
            "ad_number": bid.ad_number,
            "title": bid.title,
            "agency": bid.agency,
            "ad_type": bid.ad_type,
            "status": bid.status,
            "ad_date": bid.ad_date,
            "open_date": bid.open_date,
            "close_date": bid.close_date,
            "description": bid.description,
            "role": "OWNER",
            "match_strength": bid.match_strength,
            "score": bid.primary_score if lane != OTHER else bid.closest_niche_score,
            "code_points": own.code_points if own else "",
            "title_points": own.title_points if own else "",
            "scope_points": own.scope_points if own else "",
            **niche_columns,
            "primary_niche": bid.primary_niche,
            "secondary_niches": [f"{n} ({s})" for n, s in (bid.secondary_niches or [])],
            "matched_codes": bid.matched_codes or [],
            "code_source": bid.code_source,
            "matched_keywords": own.matched_keywords if own else [],
            "deliverables_detected": (bid.deliverables_detected or {}).get(lane, []),
            "suppressed_terms": own.suppressed_terms if own else [],
            "flags": bid.flags or [],
            "documents": bid.documents or [],
            "document_chars": bid.document_chars,
        }
        if lane == OTHER:
            row.update(
                other_reason=bid.other_reason,
                closest_niche=bid.closest_niche,
                closest_niche_score=bid.closest_niche_score,
            )
        lanes.setdefault(lane, []).append(row)

        if config.cross_listing:
            for niche_key, score_value in (bid.secondary_niches or []):
                secondary = ad_scores.get(niche_key)
                lanes.setdefault(niche_key, []).append({
                    **row,
                    "role": "CROSS-LISTED",
                    "score": score_value,
                    "code_points": secondary.code_points if secondary else "",
                    "title_points": secondary.title_points if secondary else "",
                    "scope_points": secondary.scope_points if secondary else "",
                    "matched_keywords": secondary.matched_keywords if secondary else [],
                })

    build_workbook(config, lanes, Path(out_path))
    return len(bids)
