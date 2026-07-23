"""Thin bridge between the hub and the vendored SAM bid evaluator.

The evaluation logic itself is NOT reimplemented here — it lives untouched in
server/scrappers/sam/evaluator.py. This module only:
  * loads the vendored SAM config.yml (the `sam` section) once,
  * injects the live kill-word list from the `eval_config` table (so UI edits
    take effect without a restart, exactly like the sam-septa route did),
  * calls the vendored ``evaluate_bid``,
  * seeds the eval_config table from the config.yml defaults the first time.
"""

import logging
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from app.db import SessionLocal
from app.scrapers.evalconfig.models import (
    CATEGORY_ALLOWED_SERVICE,
    CATEGORY_EXCLUDED_SERVICE,
    CATEGORY_KILL_WORD,
    EvalConfig,
)
from app.scrapers.sam.engine.evaluator import evaluate_bid

logger = logging.getLogger(__name__)

# The vendored SAM engine's config lives inside its engine package; load the
# `sam` section once, resolved relative to this file (app/scrapers/sam/).
_SAM_CFG_PATH = Path(__file__).resolve().parent / "engine" / "config.yml"
with open(_SAM_CFG_PATH, "r", encoding="utf-8") as _f:
    SAM_CONFIG: dict[str, Any] = (yaml.safe_load(_f) or {}).get("sam", {})


def _kill_words(session) -> list[str]:
    rows = session.execute(
        select(EvalConfig).where(EvalConfig.category == CATEGORY_KILL_WORD)
    ).scalars().all()
    return [r.value for r in rows]


def evaluate(notice_id: str, full_text: str, naics_code: str = "", title: str = "") -> dict:
    """Evaluate one bid with the vendored funnel, using live DB kill-words.

    Mirrors the sam-septa route: copy the config, override only `kill_words` with
    the DB rows, and delegate to the vendored ``evaluate_bid``.
    """
    session = SessionLocal()
    try:
        kill_words = _kill_words(session)
    finally:
        session.close()

    cfg = dict(SAM_CONFIG)
    cfg["evaluation"] = dict(cfg.get("evaluation", {}))
    cfg["evaluation"]["kill_words"] = kill_words
    return evaluate_bid(notice_id, full_text, cfg, naics_code=naics_code, title=title)


def seed_defaults() -> None:
    """Populate eval_config from the config.yml defaults the first time (idempotent).

    The evaluator reads kill-words from the DB, so without a seed a fresh install
    would evaluate with an empty kill-word list. Only seeds categories that are
    currently empty, so user edits are never overwritten.
    """
    evaluation = SAM_CONFIG.get("evaluation", {})
    defaults = {
        CATEGORY_KILL_WORD: evaluation.get("kill_words", []),
        CATEGORY_EXCLUDED_SERVICE: evaluation.get("excluded_services", []),
        CATEGORY_ALLOWED_SERVICE: evaluation.get("allowed_services", []),
    }
    session = SessionLocal()
    try:
        for category, values in defaults.items():
            existing = session.execute(
                select(EvalConfig.value).where(EvalConfig.category == category)
            ).scalars().all()
            if existing:
                continue  # category already populated — leave user edits alone
            for value in values:
                v = str(value).strip().lower()
                if v:
                    session.add(EvalConfig(category=category, value=v))
        session.commit()
        logger.info("seeded eval_config defaults where empty")
    except Exception:  # noqa: BLE001 — seeding must never break a scrape
        session.rollback()
        logger.exception("eval_config seed failed")
    finally:
        session.close()
