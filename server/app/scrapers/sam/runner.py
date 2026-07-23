"""Background-task runner that drives the vendored SAM engine and stores its
output the hub way.

The scrape + evaluation logic is the vendored engine's (server/scrappers/sam/);
this only bridges its `_on_bid_extracted` callback to the evaluator and the DB,
tracks progress via run_manager, and writes the per-run Excel into a
date-bucketed folder like the other portals.
"""

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core import run_manager
from app.core.filenames import sanitize_filename
from app.scrapers.sam import export
from app.services.notifier import notify_scrape_completion
from app.scrapers.sam.evaluation import evaluate, seed_defaults
from app.scrapers.sam.engine.sam_scraper import SAMGovScraper

logger = logging.getLogger(__name__)

# Live scrapers keyed by run_id, so the screenshot/stop endpoints can reach the
# running Selenium session. Cleaned up when the run ends.
_live: dict[str, SAMGovScraper] = {}
_stops: dict[str, threading.Event] = {}


def get_live_scraper(run_id: str) -> SAMGovScraper | None:
    return _live.get(run_id)


def request_stop(run_id: str) -> bool:
    """Signal a graceful stop; returns False if the run isn't active."""
    ev = _stops.get(run_id)
    if ev is None:
        return False
    ev.set()
    return True


def _bid_to_record(bid: dict[str, Any]) -> tuple[str, str, str, str]:
    notice_id = bid.get("Notice ID") or bid.get("Notice Title", "unknown")
    title = bid.get("Notice Title", "")
    naics_code = bid.get("NAICS Code", "")
    full_text = bid.get("Full Text", "")
    return notice_id, title, naics_code, full_text


def execute_run(
    run_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
    naics_codes: list[str] | None = None,
    award_notice: bool = False,
    headless: bool = True,
) -> None:
    seed_defaults()  # ensure the evaluator has its kill-word list on a fresh DB
    run_manager.update_run(run_id, status="running")
    _save_run_row(run_id)

    records: list[dict[str, Any]] = []
    stop_event = threading.Event()
    _stops[run_id] = stop_event
    scraper: SAMGovScraper | None = None
    run_dir = run_manager.run_folder(run_id)

    def _on_bid(bid: dict[str, Any]) -> None:
        notice_id, title, naics_code, full_text = _bid_to_record(bid)
        try:
            result = evaluate(notice_id, full_text, naics_code=naics_code, title=title)
            decision = result.get("decision", "PENDING")
            reason = result.get("reason", "")
        except Exception as exc:  # noqa: BLE001 — an eval error must not drop the bid
            decision, reason = "PENDING", f"Evaluation error: {exc}"

        record = {
            "notice_id": notice_id,
            "title": bid.get("Notice Title", ""),
            "department": bid.get("Department/Ind. Agency", ""),
            "subtier": bid.get("Subtier", ""),
            "office": bid.get("Office", ""),
            "description": bid.get("Description", ""),
            "updated_date": bid.get("Updated Date", ""),
            "bid_repeat_count": int(bid.get("bid_repeat_count", 0) or 0),
            "naics_code": bid.get("NAICS Code", ""),
            "naics_title": bid.get("NAICS Title", ""),
            "date_offers_due": bid.get("Date Offers Due", ""),
            "published_date": bid.get("Published Date", ""),
            "decision": decision,
            "reason": reason,
        }
        records.append(record)
        run_manager.add_bid_result(run_id, {**record, "documents": [], "error": None})

    try:
        scraper = SAMGovScraper(
            headless=headless,
            date_filter=date_from,
            date_to=date_to,
            naics_codes=naics_codes or [],
            award_notice=award_notice,
        )
        scraper._stop_event = stop_event
        scraper.skip_csv = True            # DB-only; no CSV files
        scraper._on_bid_extracted = _on_bid
        _live[run_id] = scraper

        run_manager.update_run(run_id, step="scraping")
        scraper.run(max_records=1000)

        if not records:
            run_manager.update_run(run_id, no_results=True)

        # Persist (best-effort) then Excel from DB, or from records on DB failure.
        run = run_manager.get_run(run_id) or {"run_id": run_id}
        db_ok = True
        try:
            stored = export.save_bids(run, records)
            run_manager.update_run(run_id, bids_stored_in_db=stored)
        except Exception:  # noqa: BLE001
            db_ok = False
            logger.exception("[run %s] SAM DB save failed", run_id)
            run_manager.add_error(run_id, "db save failed (see logs)")

        run_manager.update_run(run_id, step="generating_excel")
        search = (run.get("search") or "sam bids").strip()
        name = sanitize_filename(f"SAM_({search})", max_length=150)
        excel_path = _unique_path(run_dir / f"{name}.xlsx")
        try:
            if db_ok:
                export.generate_excel(run_id, excel_path)
            else:
                export.generate_excel_from_records(records, excel_path)
            run_manager.update_run(run_id, excel_path=str(excel_path), excel_exported=True)
        except Exception:  # noqa: BLE001
            logger.exception("[run %s] SAM Excel generation failed", run_id)
            run_manager.add_error(run_id, "excel generation failed (see logs)")

        stopped = stop_event.is_set()
        run_manager.update_run(
            run_id, status="completed", step="stopped" if stopped else "done"
        )
        # Email/S3 notification on a successful (non-stopped) completion.
        if not stopped:
            notify_scrape_completion(run_id, "sam", len(records))
    except Exception as exc:  # noqa: BLE001 — a failed run must be reported, not crash the worker
        logger.exception("[run %s] SAM run failed", run_id)
        run_manager.add_error(run_id, str(exc)[:500])
        run_manager.update_run(run_id, status="failed", step="failed")
    finally:
        if scraper is not None:
            try:
                scraper.close()
            except Exception:  # noqa: BLE001 — best-effort teardown
                pass
        _live.pop(run_id, None)
        _stops.pop(run_id, None)
        run_manager.update_run(run_id, finished_at=datetime.now().isoformat())
        _save_run_row(run_id)


def _save_run_row(run_id: str) -> None:
    run = run_manager.get_run(run_id)
    if not run:
        return
    try:
        export.save_run(run)
    except Exception:  # noqa: BLE001
        logger.exception("[run %s] SAM save_run failed", run_id)


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
