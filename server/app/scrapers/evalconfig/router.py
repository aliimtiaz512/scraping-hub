"""Eval-config routes — manage the SAM evaluator's kill words, excluded services
(Rule B) and allowed services (Rule C) via the DB.

Ported from the sam-septa routes/eval_config.py onto the hub's SQLAlchemy
session dependency. Behaviour (endpoints, dedupe, lowercase-normalisation) is
identical so the existing evaluator and any future Evaluator Settings UI work
unchanged.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import get_session
from app.scrapers.evalconfig.models import (
    CATEGORY_ALLOWED_SERVICE,
    CATEGORY_EXCLUDED_SERVICE,
    CATEGORY_KILL_WORD,
    EvalConfig,
)

router = APIRouter(prefix="/eval-config", tags=["eval-config"])

_ERR_EMPTY = "value must not be empty"


class EvalConfigValueRequest(BaseModel):
    value: str


def _add_value(session: Session, category: str, raw: str) -> str:
    value = raw.strip().lower()
    if not value:
        raise HTTPException(status_code=422, detail=_ERR_EMPTY)
    existing = session.execute(
        select(EvalConfig).where(EvalConfig.category == category, EvalConfig.value == value)
    ).scalar_one_or_none()
    if not existing:
        session.add(EvalConfig(category=category, value=value))
        session.commit()
    return value


def _delete_value(session: Session, category: str, raw: str, label: str) -> str:
    value = raw.strip().lower()
    row = session.execute(
        select(EvalConfig).where(EvalConfig.category == category, EvalConfig.value == value)
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"{label} '{value}' not found")
    session.delete(row)
    session.commit()
    return value


@router.get("")
def get_eval_config(session: Session = Depends(get_session)) -> dict:
    """Return all kill words, excluded services, and allowed services from the DB."""
    try:
        rows = session.execute(select(EvalConfig)).scalars().all()
    except OperationalError as exc:
        raise HTTPException(
            status_code=503, detail="Database unavailable — check DATABASE_URL in server/.env"
        ) from exc
    return {
        "kill_words": sorted(r.value for r in rows if r.category == CATEGORY_KILL_WORD),
        "excluded_services": sorted(r.value for r in rows if r.category == CATEGORY_EXCLUDED_SERVICE),
        "allowed_services": sorted(r.value for r in rows if r.category == CATEGORY_ALLOWED_SERVICE),
    }


# -- kill words -------------------------------------------------------------

@router.post("/kill-words")
def add_kill_word(body: EvalConfigValueRequest, session: Session = Depends(get_session)) -> dict:
    return {"success": True, "value": _add_value(session, CATEGORY_KILL_WORD, body.value)}


@router.delete("/kill-words/{value}")
def delete_kill_word(value: str, session: Session = Depends(get_session)) -> dict:
    return {"success": True, "value": _delete_value(session, CATEGORY_KILL_WORD, value, "Kill word")}


# -- excluded services (Rule B) ---------------------------------------------

@router.post("/excluded-services")
def add_excluded_service(body: EvalConfigValueRequest, session: Session = Depends(get_session)) -> dict:
    return {"success": True, "value": _add_value(session, CATEGORY_EXCLUDED_SERVICE, body.value)}


@router.delete("/excluded-services/{value}")
def delete_excluded_service(value: str, session: Session = Depends(get_session)) -> dict:
    return {"success": True, "value": _delete_value(session, CATEGORY_EXCLUDED_SERVICE, value, "Excluded service")}


# -- allowed services (Rule C) ----------------------------------------------

@router.post("/allowed-services")
def add_allowed_service(body: EvalConfigValueRequest, session: Session = Depends(get_session)) -> dict:
    return {"success": True, "value": _add_value(session, CATEGORY_ALLOWED_SERVICE, body.value)}


@router.delete("/allowed-services/{value}")
def delete_allowed_service(value: str, session: Session = Depends(get_session)) -> dict:
    return {"success": True, "value": _delete_value(session, CATEGORY_ALLOWED_SERVICE, value, "Allowed service")}
