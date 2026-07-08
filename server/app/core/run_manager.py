"""Tracks scrape runs across all scrapers: status, progress, output folders.

Runs are held in memory keyed by run_id. The dict is intentionally loose so each
scraper can attach its own fields (MFMP adds category/priority/codes; RideMetro
adds a folder label). Common keys — status, step, counts, errors, bids — are used
uniformly by every scraper and the API.
"""

import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.filenames import sanitize_filename  # noqa: F401 — re-exported for scrapers

_lock = threading.Lock()
_runs: dict[str, dict[str, Any]] = {}


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
        if run:
            run.update(fields)


def add_error(run_id: str, message: str) -> None:
    with _lock:
        run = _runs.get(run_id)
        if run:
            run["errors"].append(message)


def add_bid_result(run_id: str, bid: dict[str, Any]) -> None:
    """Append a per-bid result and refresh derived counts.

    A bid may carry a `documents` list (files/zips saved for it); the total
    across all bids becomes documents_downloaded.
    """
    with _lock:
        run = _runs.get(run_id)
        if run:
            run["bids"].append(bid)
            run["bids_processed"] = len(run["bids"])
            docs = sum(len(b.get("documents", [])) for b in run["bids"])
            if docs:
                run["documents_downloaded"] = docs


def run_folder(run_id: str) -> Path:
    run = get_run(run_id)
    if not run:
        raise KeyError(f"Unknown run: {run_id}")
    return Path(run["folder"])
