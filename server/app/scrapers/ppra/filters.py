"""Filter normalization and dropdown option caching for PPRA."""

import hashlib
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import SessionLocal
from app.scrapers.ppra.models import PpraFilterOption
from app.scrapers.ppra.schemas import PpraFilters

logger = logging.getLogger(__name__)


def compute_filters_hash(filters: PpraFilters) -> str:
    """Compute sha256 hash of canonicalized filter values."""
    dump = filters.model_dump()
    canon = {k: ("" if v is None or v == "ALL" else str(v).strip().lower()) for k, v in dump.items()}
    encoded = json.dumps(canon, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def get_cached_filter_options(kind: str | None = None) -> list[dict[str, Any]]:
    """Fetch filter options stored in ppra_filter_options."""
    session = SessionLocal()
    try:
        stmt = select(PpraFilterOption).order_by(PpraFilterOption.sort_order, PpraFilterOption.label)
        if kind:
            stmt = stmt.where(PpraFilterOption.kind == kind)
        rows = session.execute(stmt).scalars().all()
        return [{"kind": r.kind, "value": r.value, "label": r.label, "sort_order": r.sort_order} for r in rows]
    finally:
        session.close()


def save_filter_options(options_by_kind: dict[str, list[dict[str, str]]]) -> int:
    """Upsert filter options discovered from the site."""
    session = SessionLocal()
    count = 0
    try:
        for kind, options in options_by_kind.items():
            for idx, opt in enumerate(options):
                stmt = pg_insert(PpraFilterOption).values(
                    kind=kind,
                    value=opt["value"],
                    label=opt["label"],
                    sort_order=idx,
                ).on_conflict_do_update(
                    index_elements=[PpraFilterOption.kind, PpraFilterOption.value],
                    set_={"label": opt["label"], "sort_order": idx},
                )
                session.execute(stmt)
                count += 1
        session.commit()
        return count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

