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

# run_ids the user asked to stop. Once a run is in here its terminal status is
# locked to "stopped": update_run ignores later status/step writes and add_error
# suppresses the browser-teardown errors a forced stop produces, so a
# user-stopped run reads as a clean "Stopped" instead of a scary "Failed".
_stopped: set[str] = set()

# run_ids the user asked to pause. A worker parks at its next checkpoint while
# its id is in here and carries on the moment it leaves — see
# BaseScraper.raise_if_stopped, which is the one place that waits.
#
# A set rather than a status field because pause has to be answerable from a
# scraper thread thousands of times a run, without a dictionary lookup racing a
# status write from the API thread.
_paused: set[str] = set()
# Signalled whenever `_paused` shrinks, so a parked worker wakes at once on
# Resume rather than at the end of a poll interval.
_resumed = threading.Condition(_lock)

# Terminal statuses — a run in any of these is finished and no longer active.
TERMINAL_STATUSES = ("completed", "failed", "stopped")
# Statuses a run can be stopped from: queued (waiting for a slot — see
# app/core/jobs) and everything up to and including execution.
STOPPABLE_STATUSES = ("pending", "queued", "running", "paused")
# Statuses a run can be paused from. Only a run that is actually executing:
# pausing a queued run would mean holding a slot open for work that has not
# started, when simply leaving it queued already achieves that.
PAUSABLE_STATUSES = ("running",)


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
            if run.get("status") in STOPPABLE_STATUSES:
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
    """Create and return a per-run scratch folder under the temp workspace.

    Everything a run produces (bid documents, browser download staging,
    DB-outage fallback sheets) lands here; on completion the folder is zipped
    into settings.archive_root and deleted — nothing is left on local disk.
    """
    folder = settings.work_root / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def create_run(scraper: str, folder: Path, fields: dict[str, Any] | None = None) -> dict[str, Any]:
    """Register a new run. `scraper` is the portal key (e.g. 'myflorida')."""
    run_id = uuid.uuid4().hex[:12]
    run = {
        "run_id": run_id,
        "scraper": scraper,
        # pending -> queued (waiting for a slot) -> running -> completed |
        # failed | stopped. See app/core/jobs for who moves it between them.
        "status": "pending",
        "step": "queued",
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "folder": str(folder),
        "bids_found": 0,
        "bids_processed": 0,
        "documents_downloaded": 0,
        "errors": [],
        # Non-fatal notices (e.g. a keyword that matched nothing). Kept separate
        # from errors so a run full of legitimate zero-result searches still reads
        # as completed rather than failed.
        "warnings": [],
        # Set true when every search pass returned zero rows — lets the UI say
        # "search worked, portal has nothing" instead of a silent green tick.
        "no_results": False,
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


def request_stop(run_id: str) -> bool:
    """Ask an in-flight run to stop. Returns False if the run isn't active.

    Locks the run's terminal status to "stopped" immediately so the UI reflects
    the stop at once and a later status write from the worker (which is about to
    unwind through its own error handling as its browser is torn down) can't flip
    it to "failed". The actual browser interruption is done by the caller via
    live.stop(); this only owns the run-state side.
    """
    with _lock:
        run = _runs.get(run_id)
        if not run or run.get("status") not in STOPPABLE_STATUSES:
            return False
        _stopped.add(run_id)
        # A parked worker is asleep in `await_resume`; releasing it here is what
        # lets a paused run be stopped outright instead of having to be resumed
        # first only to be stopped a moment later.
        _paused.discard(run_id)
        _resumed.notify_all()
        run["status"] = "stopped"
        run["step"] = "stopped"
        run["finished_at"] = run.get("finished_at") or datetime.now().isoformat()
        snapshot = dict(run)
    _persist(snapshot)
    return True


def is_stop_requested(run_id: str) -> bool:
    with _lock:
        return run_id in _stopped


def request_pause(run_id: str) -> bool:
    """Ask an executing run to park at its next checkpoint. False if it can't.

    The run keeps its slot and its browser — this is a hold, not a teardown, and
    that is what makes resuming exact: the worker is still standing where it
    stopped, so it carries on at the next record rather than replaying anything.
    What it gives up is the network and the CPU, which is what someone pausing a
    long SAM run to get a SEPTA delivery out actually wants back.
    """
    with _lock:
        run = _runs.get(run_id)
        if not run or run.get("status") not in PAUSABLE_STATUSES:
            return False
        _paused.add(run_id)
        run["status"] = "paused"
        run["paused_at"] = datetime.now().isoformat()
        snapshot = dict(run)
    _persist(snapshot)
    logger.info("[run %s] pause requested", run_id)
    return True


def request_resume(run_id: str) -> bool:
    """Release a parked run. False if it was not paused.

    Wakes the worker immediately rather than letting it find out on its next
    poll: a resume that takes effect in a second reads as a control, and one
    that takes effect in thirty reads as a bug.
    """
    with _lock:
        run = _runs.get(run_id)
        if not run or run_id not in _paused:
            return False
        _paused.discard(run_id)
        if run.get("status") == "paused":
            run["status"] = "running"
        run["resumed_at"] = datetime.now().isoformat()
        snapshot = dict(run)
        _resumed.notify_all()
    _persist(snapshot)
    logger.info("[run %s] resumed", run_id)
    return True


def is_paused(run_id: str) -> bool:
    with _lock:
        return run_id in _paused


def await_resume(run_id: str, timeout: float = 1.0) -> bool:
    """Block while this run is paused. True if it is still paused on return.

    Bounded rather than indefinite so a parked worker stays responsive to a
    *stop* — pausing and then deciding to abandon the run is an ordinary thing
    to do, and a worker asleep on a condition with no timeout would not notice
    until someone resumed it first.
    """
    with _lock:
        if run_id not in _paused:
            return False
        _resumed.wait(timeout)
        return run_id in _paused


def paused_runs() -> set[str]:
    with _lock:
        return set(_paused)


def update_run(run_id: str, **fields: Any) -> None:
    with _lock:
        run = _runs.get(run_id)
        if not run:
            return
        if run_id in _stopped:
            # A stopped run stays stopped: drop status/step reversions from the
            # worker's teardown, but let finished_at/counts through.
            fields = {k: v for k, v in fields.items() if k not in ("status", "step")}
        run.update(fields)
        snapshot = dict(run)
    _persist(snapshot)


def mark_partial(run_id: str, kept: int) -> None:
    """Record that a stopped run kept the rows it had gathered.

    `partial_results` is what the console keys its Download button off, and it
    is deliberately a separate field from the status rather than a third
    terminal status. The run **is** stopped — the user ended it and rows are
    missing — and saying anything else on the badge would hide the one fact a
    reviewer needs. What the flag adds is that there is nevertheless something
    to download, which "stopped" alone has never distinguished.

    Written through `update_run`, so the stopped-run guard there keeps the
    status and step exactly where `request_stop` put them.
    """
    update_run(
        run_id,
        partial_results=True,
        partial_record_count=kept,
        stopped_at=datetime.now().isoformat(),
    )


def add_error(run_id: str, message: str) -> None:
    with _lock:
        run = _runs.get(run_id)
        if not run:
            return
        if run_id in _stopped:
            # The WebDriver errors a forced stop produces aren't real failures.
            return
        run["errors"].append(message)
        snapshot = dict(run)
    _persist(snapshot)


def add_warning(run_id: str, message: str) -> None:
    """Append a non-fatal notice (parallel to add_error). Deduplicated so a
    repeated message doesn't stack up."""
    with _lock:
        run = _runs.get(run_id)
        if not run:
            return
        warnings = run.setdefault("warnings", [])
        if message not in warnings:
            warnings.append(message)
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


def remove_empty_folder(run_id: str) -> None:
    """Delete the run's workspace folder if nothing was written into it, plus
    any now-empty parents up to the workspace root.

    Successful runs are cleaned up by exports.archive_run; this catches runs
    that failed before producing anything. Only folders inside the temp
    workspace are ever touched — legacy runs pointing into data/documents are
    left alone.
    """
    try:
        folder = run_folder(run_id).resolve()
    except KeyError:
        return
    root = settings.work_root
    try:
        current = folder
        while current != root and root in current.parents:
            if not current.is_dir() or any(current.iterdir()):
                break
            current.rmdir()
            current = current.parent
    except OSError:  # noqa: BLE001 — tidying is best-effort, never fatal
        logger.debug("could not tidy empty run folder for %s", run_id, exc_info=True)
