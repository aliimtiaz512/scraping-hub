"""Keeping the rows a stopped run had already gathered.

Pressing Stop used to cost a run everything it had found. The reason was
structural rather than a bug anyone wrote: every scraper's `run()` does its work
in a loop and then, *after* the loop, saves to the database, writes the sheet and
packages the archive. `StopRequested` unwinds out of the loop, so it unwinds past
all three. A user who stopped a 300-bid run at bid 75 to get an urgent one out
paid for it with all 75.

Two scrapers had already noticed and worked around it — SAM and the MyFlorida
sweep both flushed on stop and then marked the run **completed**, because the
download endpoint would not serve anything else. That is the wrong lie to tell:
the run did not complete, and a console that says it did hides the one thing the
reviewer needs to know. The status stays `stopped`; what changed is that a
stopped run can now carry results and say so (`partial_results`).

This module is the half of that which is the same everywhere. Most portals keep
their rows in `self._records` and finish by calling `export.save_bids` with a
disk sheet as the fallback for a database that is down — `flush_records` is that
sequence, so each scraper's `flush_partial` is a couple of lines naming its own
two functions rather than a sixth copy of the same error handling. The two are
passed as callables rather than taken off an `export` module, because the
signatures are not quite uniform — SEPTA's sheet writer takes its open-bids
list as a third argument — and a helper that could not fit SEPTA would be a
helper five portals used and the sixth quietly worked around.

Portals whose shape differs (MyFlorida's per-keyword workbook merge, BidNet's
shared session root) implement `flush_partial` themselves.
"""

from __future__ import annotations

import logging
from pathlib import Path
from collections.abc import Callable
from typing import Any

from app.core import run_manager
from app.core.filenames import sanitize_filename

logger = logging.getLogger(__name__)


def unique_path(candidate: Path) -> Path:
    """`candidate`, or the first `name (n).xlsx` beside it that is free.

    A stopped run can be re-stopped — a second Stop while the first is still
    unwinding — and two identical same-day searches land on one name anyway.
    Neither should overwrite a sheet that is the only copy of its rows.
    """
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for counter in range(2, 1000):
        alternative = candidate.with_name(f"{stem} ({counter}){suffix}")
        if not alternative.exists():
            return alternative
    return candidate


def flush_records(
    scraper: Any,
    records: list[dict[str, Any]],
    save_bids: Callable[[dict[str, Any], list[dict[str, Any]]], int],
    write_sheet: Callable[[list[dict[str, Any]], Path], Any],
    sheet_name: str,
) -> int:
    """Save a stopped run's rows the way its completed path would. Returns the count.

    The database first, because that is what `exports.excel_bytes` regenerates
    the download from; a disk sheet only when the database refused, since then
    the file is the only copy the rows have. This mirrors the completed path
    deliberately — a stopped run's spreadsheet should be the same spreadsheet,
    one page shorter, not a second format nobody else writes.

    `db_save_failed` matters more here than it does on a completed run. It tells
    the packaging step to ship the disk sheet rather than regenerate from an
    empty table, and a stopped run is exactly when a half-finished database save
    is most likely.
    """
    if not records:
        return 0

    run = run_manager.get_run(scraper.run_id) or {"run_id": scraper.run_id}
    try:
        stored = save_bids(run, records)
        run_manager.update_run(
            scraper.run_id, bids_stored_in_db=stored, excel_exported=True
        )
        logger.info(
            "[run %s] flushed %d partial record(s) to the database",
            scraper.run_id, stored,
        )
        return len(records)
    except Exception:  # noqa: BLE001 — a stopped run must still deliver its rows
        logger.exception("[run %s] partial DB save failed", scraper.run_id)
        run_manager.add_warning(
            scraper.run_id,
            "the database save failed for this stopped run — its spreadsheet is "
            "the only copy of the rows it gathered",
        )
        run_manager.update_run(scraper.run_id, db_save_failed=True)

    path = unique_path(
        Path(scraper.run_dir) / f"{sanitize_filename(sheet_name, max_length=150)}.xlsx"
    )
    try:
        write_sheet(records, path)
    except Exception:  # noqa: BLE001 — the archive still carries whatever is on disk
        logger.exception("[run %s] partial Excel generation failed", scraper.run_id)
        run_manager.add_error(
            scraper.run_id, "could not write the stopped run's spreadsheet (see logs)"
        )
        return 0

    run_manager.update_run(
        scraper.run_id, excel_path=str(path), excel_exported=True
    )
    return len(records)
