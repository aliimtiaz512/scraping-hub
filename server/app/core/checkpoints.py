"""Where a run had got to, so a resume does not start from nothing.

Every scraper here is a browser driving a portal, and that shapes what a
checkpoint can honestly be. **A live Chrome session cannot be written to
Postgres.** A paused BidNet run holding a signed-in browser on a filtered
results page is not a value; it is a process. So "resume" means two genuinely
different things depending on whether that process is still alive, and both are
real:

* **The worker is still there** (the ordinary Pause button). It parks at its next
  checkpoint, the browser idles, and resuming carries straight on from record
  246 — no replay, no re-login, no duplicate work. Exact, and free.
* **The worker is gone** (a network drop, a restart, an OOM kill). Nothing can
  carry on; a resume is a *new* run that signs in again, re-establishes the
  search, and skips what the old one finished. That is what this module stores.

The second case is why `processed` holds identifiers and not just a count. A
count is only enough if the portal hands back the same rows in the same order on
the way in again, and none of these portals promise that — BidNet's list shifts
under pagination, MyFlorida's sorts by a date that keeps moving. Skipping by
membership is the only version that cannot silently re-extract or silently drop.

Everything here is best-effort by construction. A checkpoint that fails to write
must never fail the run it was describing: the worst case is a resume that
repeats work, and that is enormously better than a scrape that died because its
bookmark could not be saved.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# How many identifiers a checkpoint keeps. A sweep can reach a couple of
# thousand records and each id is short, so the whole list is well under the
# size at which a JSONB column becomes a problem — but it is bounded rather
# than unbounded, because "the checkpoint grew until the write timed out" is a
# failure that would only ever show up on the longest and most valuable runs.
# Past the cap the oldest ids are dropped: a resume may then repeat a little of
# the earliest work, which is the least costly thing to get wrong.
MAX_PROCESSED = 20_000

# Guards the in-memory mirror below. Checkpoints are written from scraper
# threads and read from API threads.
_lock = threading.Lock()


@dataclass
class Checkpoint:
    """A run's position, as the scraper sees it."""

    run_id: str
    scraper: str = ""
    records_done: int = 0
    records_total: int | None = None
    position: dict[str, Any] = field(default_factory=dict)
    processed: set[str] = field(default_factory=set)

    def as_row(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scraper": self.scraper or None,
            "records_done": self.records_done,
            "records_total": self.records_total,
            "position": self.position,
            # Sorted so a stored checkpoint is diffable between writes; the set
            # is what carries the meaning, the order is for whoever reads it.
            "processed": sorted(self.processed),
        }

    def summary(self) -> dict[str, Any]:
        """What the console shows: the numbers, never the id list."""
        return {
            "records_done": self.records_done,
            "records_total": self.records_total,
            "position": self.position,
        }


# run_id -> its checkpoint. The DB is the durable copy; this is the one the
# scraper actually touches, so a checkpoint write is a dict update and a
# best-effort flush rather than a database round trip per record.
_live: dict[str, Checkpoint] = {}


def get(run_id: str) -> Checkpoint | None:
    with _lock:
        return _live.get(run_id)


def start(run_id: str, scraper: str = "") -> Checkpoint:
    """Begin (or adopt) this run's checkpoint."""
    with _lock:
        existing = _live.get(run_id)
        if existing is not None:
            return existing
        checkpoint = Checkpoint(run_id=run_id, scraper=scraper)
        _live[run_id] = checkpoint
    return checkpoint


def record(
    run_id: str,
    *,
    identifier: str | None = None,
    records_done: int | None = None,
    records_total: int | None = None,
    position: dict[str, Any] | None = None,
    flush: bool = False,
) -> Checkpoint | None:
    """Advance this run's checkpoint. Returns it, or None if there isn't one.

    `identifier` marks one record finished — the count follows from the set
    unless a caller overrides it, so a scraper cannot report 246 done while
    naming 245. `flush` forces the durable write; ordinary calls only update
    memory, and the caller decides when a round trip is worth it (see `save`).
    """
    with _lock:
        checkpoint = _live.get(run_id)
        if checkpoint is None:
            return None
        if identifier:
            checkpoint.processed.add(identifier)
            if len(checkpoint.processed) > MAX_PROCESSED:
                # Bounded: drop the oldest by sort order. A resume may repeat a
                # little of the earliest work, which is the cheapest thing here
                # to get wrong.
                excess = len(checkpoint.processed) - MAX_PROCESSED
                for stale in sorted(checkpoint.processed)[:excess]:
                    checkpoint.processed.discard(stale)
            checkpoint.records_done = max(checkpoint.records_done, len(checkpoint.processed))
        if records_done is not None:
            checkpoint.records_done = records_done
        if records_total is not None:
            checkpoint.records_total = records_total
        if position is not None:
            checkpoint.position = dict(position)
        snapshot = checkpoint.as_row() if flush else None
    if snapshot is not None:
        _flush(snapshot)
    return checkpoint


def save(run_id: str) -> None:
    """Force this run's checkpoint to the database.

    Called at the moments where losing the position would actually cost
    something: a pause, a stop, the end of a page, the end of a run.
    """
    with _lock:
        checkpoint = _live.get(run_id)
        snapshot = checkpoint.as_row() if checkpoint else None
    if snapshot:
        _flush(snapshot)


def load(run_id: str) -> Checkpoint | None:
    """Read a stored checkpoint back — the cold-resume path.

    Returns None when there is nothing stored, which a caller must read as
    "start from the beginning" rather than as an error: a run that died before
    its first record has no bookmark, and beginning again is correct.
    """
    try:
        from sqlalchemy import select

        from app.core.models import Checkpoint as Row
        from app.db import SessionLocal

        session = SessionLocal()
        try:
            row = session.execute(select(Row).where(Row.run_id == run_id)).scalar_one_or_none()
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — an unreadable checkpoint is not an error
        logger.exception("could not load checkpoint for %s", run_id)
        return None
    if row is None:
        return None
    return Checkpoint(
        run_id=row.run_id,
        scraper=row.scraper or "",
        records_done=row.records_done or 0,
        records_total=row.records_total,
        position=dict(row.position or {}),
        processed=set(row.processed or []),
    )


def adopt(run_id: str, previous: Checkpoint, scraper: str = "") -> Checkpoint:
    """Begin this run from what a previous one had already done.

    The new run gets its own id — it is a different run, with its own browser,
    its own folder and its own row in the console — but it inherits the previous
    run's `processed` set, which is the whole point: what it must not do is
    extract those records again.
    """
    checkpoint = Checkpoint(
        run_id=run_id,
        scraper=scraper or previous.scraper,
        records_done=previous.records_done,
        records_total=previous.records_total,
        position=dict(previous.position),
        processed=set(previous.processed),
    )
    with _lock:
        _live[run_id] = checkpoint
    logger.info(
        "[run %s] resumed from a checkpoint holding %d completed record(s)",
        run_id, checkpoint.records_done,
    )
    return checkpoint


def discard(run_id: str) -> None:
    """Forget a run's checkpoint once it has finished cleanly.

    A completed run has nothing to resume, and leaving its bookmark behind would
    let a later resume skip records it never actually collected.
    """
    with _lock:
        _live.pop(run_id, None)
    try:
        from sqlalchemy import delete

        from app.core.models import Checkpoint as Row
        from app.db import SessionLocal

        session = SessionLocal()
        try:
            session.execute(delete(Row).where(Row.run_id == run_id))
            session.commit()
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — tidying is best-effort
        logger.debug("could not discard checkpoint for %s", run_id, exc_info=True)


def _flush(values: dict[str, Any]) -> None:
    """Write a checkpoint, swallowing anything that goes wrong.

    The guard lives *here*, at the call site, and not only inside `_write`.
    "A bookmark never fails the run it is describing" is the invariant this
    module exists to keep, and an invariant that depends on one function's
    internal try/except staying correct forever is one bad edit from being
    untrue. The worst case for a lost checkpoint is a resume that repeats some
    work; the worst case for a raised one is a completed scrape thrown away.
    """
    try:
        _write(values)
    except Exception:  # noqa: BLE001 — see above
        logger.exception("could not persist checkpoint for %s", values.get("run_id"))


def _write(values: dict[str, Any]) -> None:
    """Upsert one checkpoint row."""
    try:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.core.models import Checkpoint as Row
        from app.db import SessionLocal

        stmt = pg_insert(Row).values(**values).on_conflict_do_update(
            index_elements=[Row.run_id],
            set_={k: v for k, v in values.items() if k != "run_id"},
        )
        session = SessionLocal()
        try:
            session.execute(stmt)
            session.commit()
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — a bookmark must never fail the run
        logger.exception("could not persist checkpoint for %s", values.get("run_id"))
