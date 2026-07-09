"""Tracks scrape runs across all scrapers: status, progress, output folders.

Runs are held in memory keyed by run_id for fast reads, and mirrored to the
`run_state` DB table on every mutation so an in-flight run survives a server
restart (otherwise the frontend polls a lost run_id and gets a permanent 404).
The dict is intentionally loose so each scraper can attach its own fields (MFMP
adds category/priority/codes; RideMetro adds a folder label). Common keys —
status, step, counts, errors, bids — are used uniformly by every scraper and
the API.
"""

import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.filenames import sanitize_filename  # noqa: F401 — re-exported for scrapers

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_runs: dict[str, dict[str, Any]] = {}


def _persist(run: dict[str, Any]) -> None:
    """Write-through a snapshot of `run` to the run_state table. Best-effort:
    the scrape must not fail just because the DB is momentarily unavailable."""
    try:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.core.models import RunState
        from app.db import SessionLocal

        values = {
            "run_id": run["run_id"],
            "scraper": run.get("scraper"),
            "started_at": run.get("started_at"),
            "data": run,
        }
        stmt = pg_insert(RunState).values(**values).on_conflict_do_update(
            index_elements=[RunState.run_id],
            set_={"scraper": values["scraper"], "started_at": values["started_at"], "data": values["data"]},
        )
        session = SessionLocal()
        try:
            session.execute(stmt)
            session.commit()
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — persistence is best-effort, never fatal to a run
        logger.exception("could not persist run %s", run.get("run_id"))


def load_persisted_runs() -> None:
    """Rehydrate runs from the DB on startup. Any run still marked pending/running
    was cut short by the restart that just happened, so mark it failed — otherwise
    the frontend would poll it forever waiting for a terminal status."""
    try:
        from sqlalchemy import select

        from app.core.models import RunState
        from app.db import SessionLocal

        session = SessionLocal()
        try:
            rows = session.execute(select(RunState)).scalars().all()
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — a DB-less boot still serves everything else
        logger.exception("could not load persisted runs")
        return

    interrupted: list[dict[str, Any]] = []
    with _lock:
        for row in rows:
            run = dict(row.data or {})
            run_id = run.get("run_id")
            if not run_id:
                continue
            if run.get("status") in ("pending", "running"):
                run["status"] = "failed"
                run["step"] = "interrupted"
                run["finished_at"] = run.get("finished_at") or datetime.now().isoformat()
                run.setdefault("errors", []).append(
                    "Run interrupted by a server restart — please start it again."
                )
                interrupted.append(run)
            _runs[run_id] = run
    for run in interrupted:
        _persist(run)
    if interrupted:
        logger.info("marked %d interrupted run(s) as failed after restart", len(interrupted))


def make_run_folder(name: str) -> Path:
    """Create and return a per-run output folder under the documents root."""
    folder = settings.documents_root / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def create_run(scraper: str, folder: Path, fields: dict[str, Any] | None = None) -> dict[str, Any]:
    """Register a new run. `scraper` is the portal key (e.g. 'myflorida')."""
    run_id = uuid.uuid4().hex[:12]
    run = {
        "run_id": run_id,
        "scraper": scraper,
        "status": "pending",  # pending | running | completed | failed
        "step": "queued",
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "folder": str(folder),
        "bids_found": 0,
        "bids_processed": 0,
        "documents_downloaded": 0,
        "errors": [],
        "bids": [],
    }
    if fields:
        run.update(fields)
    with _lock:
        _runs[run_id] = run
        snapshot = dict(run)
    _persist(snapshot)
    return run


def get_run(run_id: str) -> dict[str, Any] | None:
    with _lock:
        run = _runs.get(run_id)
        return dict(run) if run else None


def list_runs(scraper: str | None = None) -> list[dict[str, Any]]:
    with _lock:
        runs = [dict(run) for run in _runs.values() if scraper is None or run.get("scraper") == scraper]
    runs.sort(key=lambda r: r["started_at"], reverse=True)
    return runs


def update_run(run_id: str, **fields: Any) -> None:
    with _lock:
        run = _runs.get(run_id)
        if not run:
            return
        run.update(fields)
        snapshot = dict(run)
    _persist(snapshot)


def add_error(run_id: str, message: str) -> None:
    with _lock:
        run = _runs.get(run_id)
        if not run:
            return
        run["errors"].append(message)
        snapshot = dict(run)
    _persist(snapshot)


def add_bid_result(run_id: str, bid: dict[str, Any]) -> None:
    """Append a per-bid result and refresh derived counts.

    A bid may carry a `documents` list (files/zips saved for it); the total
    across all bids becomes documents_downloaded.
    """
    with _lock:
        run = _runs.get(run_id)
        if not run:
            return
        run["bids"].append(bid)
        run["bids_processed"] = len(run["bids"])
        docs = sum(len(b.get("documents", [])) for b in run["bids"])
        if docs:
            run["documents_downloaded"] = docs
        snapshot = dict(run)
    _persist(snapshot)


def run_folder(run_id: str) -> Path:
    run = get_run(run_id)
    if not run:
        raise KeyError(f"Unknown run: {run_id}")
    return Path(run["folder"])
