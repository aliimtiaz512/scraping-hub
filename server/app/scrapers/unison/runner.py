"""Background-task runner for the vendored Unison engine.

The vendored ``UnisonMarketplaceScraper.run_scraper`` logs in, scrapes the seller
dashboard, and writes a CSV. This runner redirects that CSV into the run folder,
reads it back into records, and stores them the hub way (DB-first + Excel).
"""

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core import run_manager
from app.core.filenames import sanitize_filename
from app.scrapers.unison import export
from app.scrapers.unison.engine.unison_scraper import UnisonMarketplaceScraper

logger = logging.getLogger(__name__)

# CSV header -> model field.
_CSV_MAP = {
    "Buyer#": "buyer_number",
    "Buyer Description": "buyer_description",
    "Buyer": "buyer",
    "End Date": "end_date",
}


def _read_records(csv_path: Path) -> list[dict[str, Any]]:
    if not csv_path.exists():
        return []
    records: list[dict[str, Any]] = []
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            records.append({field: (row.get(header, "") or "") for header, field in _CSV_MAP.items()})
    return records


def execute_run(run_id: str, filter_by: str | None = None) -> None:
    run_manager.update_run(run_id, status="running", step="scraping")
    _save_run_row(run_id)

    run_dir = run_manager.run_folder(run_id)
    records: list[dict[str, Any]] = []
    try:
        scraper = UnisonMarketplaceScraper()
        # Redirect the engine's CSV into the run folder instead of the cwd.
        scraper.csv_file = str(run_dir / "unison_requests.csv")
        scraper.run_scraper(filter_by=filter_by)

        records = _read_records(Path(scraper.csv_file))
        run_manager.update_run(run_id, bids_found=len(records), bids_processed=len(records))
        for rec in records[:100]:  # mirror a preview into the live run state
            run_manager.add_bid_result(run_id, {**rec, "documents": [], "error": None})
        if not records:
            run_manager.update_run(run_id, no_results=True)

        run = run_manager.get_run(run_id) or {"run_id": run_id}
        db_ok = True
        try:
            stored = export.save_bids(run, records)
            run_manager.update_run(run_id, bids_stored_in_db=stored)
        except Exception:  # noqa: BLE001
            db_ok = False
            logger.exception("[run %s] Unison DB save failed", run_id)
            run_manager.add_error(run_id, "db save failed (see logs)")

        run_manager.update_run(run_id, step="generating_excel")
        search = (run.get("search") or "all requests").strip()
        name = sanitize_filename(f"Unison_({search})", max_length=150)
        excel_path = _unique_path(run_dir / f"{name}.xlsx")
        try:
            if db_ok:
                export.generate_excel(run_id, excel_path)
            else:
                export.generate_excel_from_records(records, excel_path)
            run_manager.update_run(run_id, excel_path=str(excel_path), excel_exported=True)
        except Exception:  # noqa: BLE001
            logger.exception("[run %s] Unison Excel generation failed", run_id)
            run_manager.add_error(run_id, "excel generation failed (see logs)")

        run_manager.update_run(run_id, status="completed", step="done")
    except Exception as exc:  # noqa: BLE001 — a failed run must be reported, not crash the worker
        logger.exception("[run %s] Unison run failed", run_id)
        run_manager.add_error(run_id, str(exc)[:500])
        run_manager.update_run(run_id, status="failed", step="failed")
    finally:
        run_manager.update_run(run_id, finished_at=datetime.now().isoformat())
        _save_run_row(run_id)


def _save_run_row(run_id: str) -> None:
    run = run_manager.get_run(run_id)
    if not run:
        return
    try:
        export.save_run(run)
    except Exception:  # noqa: BLE001
        logger.exception("[run %s] Unison save_run failed", run_id)


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    counter = 2
    while True:
        candidate = path.with_name(f"{stem} ({counter}){suffix}")
        if not candidate.exists():
            return candidate
        counter += 1
