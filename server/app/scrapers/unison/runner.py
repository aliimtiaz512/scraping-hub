"""Background-task runner for the Unison pipeline.

Two passes over one logged-in session:

  1. **The listing.** The vendored engine signs in, sets Show to 100, applies
     the run's Filter By criterion, and walks every page, returning one row per
     buy with the link to its detail page.
  2. **Each buy.** Its detail page is parsed (`detail`), its attachments fetched
     and read (`documents`), and the whole thing put through the company
     criteria (`evaluation`, over the shared SAM funnel). A buy that fails keeps
     its listing fields, carries the error, and stays in the report.

The run delivers **the spreadsheet and nothing else**. Attachments exist to be
read into the decision, so they are fetched into a scratch directory outside the
run's workspace and deleted with it at the end — see EXCEL_ONLY_PORTALS in
app/core/exports.py. Their names survive in the report.

The one thing a caller chooses is the portal's Filter By criterion. The
description-keyword exclusions and the 7-day close-date rule stay off for the
testing phase, in `filters`, which is also where each is switched back on.
"""

import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ENV_FILE, settings
from app.core import credentials, live, run_manager
from app.core.closing_filter import MIN_DAYS_UNTIL_CLOSE
from app.core.filenames import sanitize_filename
from app.scrapers.unison import detail, documents, export, filters
from app.scrapers.unison import evaluation as unison_evaluation
from app.scrapers.unison.engine.unison_scraper import UnisonMarketplaceScraper
from app.core.exports import archive_run
from app.services.notifier import notify_scrape_completion

logger = logging.getLogger(__name__)

#: How many scraped records are mirrored into the live run state for the
#: console's table. Not a processing limit — the run stores, exports and
#: evaluates every record regardless. Sized to hold any real listing whole
#: (a full unfiltered Unison day is a few hundred buys) so the console's count
#: matches the run's, and bounded only so a runaway listing cannot inflate the
#: polled run object without limit.
LIVE_PREVIEW_CEILING = 1000

# Listing row key -> model field.
_LISTING_MAP = {
    "Buyer#": "buyer_number",
    "Buyer Description": "buyer_description",
    "Buyer": "buyer",
    "End Date": "end_date",
    "Detail URL": "detail_url",
}


# What the vendored engine falls back to when the variable is unset — it types
# these into the login form rather than failing (unison_scraper.py, the
# `os.getenv(..., 'your_...')` calls). Mirrored here so the check sees exactly
# the strings the engine will use.
_ENGINE_FALLBACKS = {
    "UNISON_EMAIL": "your_email@example.com",
    "UNISON_PASSWORD": "your_password",
}


def _verify_credentials(run_id: str) -> None:
    """Confirm the credentials survived the .env parse, before we try to log in.

    The engine reads `os.environ`, which python-dotenv filled from server/.env —
    so this checks those exact strings against the literal text of the file. A
    password whose `#` was taken as a comment, whose `$name` was expanded, or
    that is shadowed by a stale export in the environment is caught here and the
    run stops. Attempting the login anyway would report a portal problem for a
    file problem, and spend a failed attempt on a real vendor account doing it.

    Nothing sensitive is logged — see app/core/credentials.fingerprint.
    """
    run_manager.update_run(run_id, step="verifying_credentials")
    loaded = {
        name: os.getenv(name, fallback) for name, fallback in _ENGINE_FALLBACKS.items()
    }
    checks = credentials.verify_all(loaded, ENV_FILE, portal="unison")

    # The hub's own settings read the same file separately; if the two loaders
    # disagree, something between them is rewriting the value.
    for name, expected in (
        ("UNISON_EMAIL", settings.unison_email),
        ("UNISON_PASSWORD", settings.unison_password),
    ):
        if expected and loaded[name] != expected:
            message = (
                f"{name} differs between the engine's environment "
                f"[{credentials.fingerprint(loaded[name])}] and app settings "
                f"[{credentials.fingerprint(expected)}] — an exported "
                f"{name} is shadowing server/.env"
            )
            logger.error("[run %s] %s", run_id, message)
            run_manager.add_error(run_id, message)
            raise RuntimeError(message)

    failures = credentials.problems(checks)
    if failures:
        for failure in failures:
            run_manager.add_error(run_id, failure)
        raise RuntimeError(failures[0])


def _listing_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The engine's listing rows in hub field names, with the Buy # split."""
    records: list[dict[str, Any]] = []
    for row in rows:
        record = {field: (row.get(key, "") or "") for key, field in _LISTING_MAP.items()}
        record["buyer_number"], record["bid_upload_count"] = detail.split_buy_number(
            record.get("buyer_number", "")
        )
        records.append(record)
    return records


def _scrape_detail(
    scraper: Any,
    session: Any,
    record: dict[str, Any],
    docs_root: Path,
) -> dict[str, Any]:
    """Open one buy's detail page, download its documents, and evaluate it.

    Returns the record enriched in place-safe fashion: General Information
    fields, line items, the sections kept for the record, the saved documents,
    and the verdict. A buy whose detail page cannot be read keeps its listing
    fields and is flagged, rather than being dropped from the report.
    """
    url = record.get("detail_url")
    if not url:
        record["error"] = "no detail link on the listing row"
        return record

    scraper.driver.get(url)
    parsed = detail.parse(scraper.driver.page_source, url)
    general = parsed["general_info"]

    # The detail page's own values win over the listing's: its Buy Description
    # is the clean one, and its End Date is a date rather than the dashboard's
    # three-line "08/06/2026 15:00 ET 4hrs 13mins" blob.
    for field in (
        "solicitation_number", "category", "subcategory", "naics",
        "naics_size_standard", "sam_contract_opportunity", "set_aside",
        "end_time", "seller_question_deadline", "delivery", "repost_reason",
    ):
        record[field] = general.get(field)
    if general.get("buy_description"):
        record["buyer_description"] = general["buy_description"]
    if general.get("end_date"):
        record["end_date"] = general["end_date"]
    if general.get("buyer"):
        record["buyer"] = general["buyer"]

    shipping = parsed["shipping"]
    record["shipping_city"] = shipping.get("city")
    record["shipping_state"] = shipping.get("state")
    record["shipping_zip"] = shipping.get("zip")

    record["line_items"] = parsed["line_items"]
    record["line_item_count"] = len(parsed["line_items"])
    record["seller_attachments_required"] = parsed["seller_attachments_required"]
    # Kept for the record and for the evaluator's body text; never exported.
    record["detail_sections"] = {
        "bidding_requirements": parsed["bidding_requirements"],
        "buy_terms": parsed["buy_terms"],
        "general_info_extra": general.get("extra", {}),
    }

    # Documents: fetched into the run's scratch directory — outside the
    # workspace, so nothing reaches the deliverable — read into the text the
    # evaluator sees, and thrown away with that directory when the run ends.
    # What the report keeps is their names.
    document_text = ""
    if parsed["attachments"]:
        folder = docs_root / sanitize_filename(record.get("buyer_number") or "buy", max_length=80)
        saved = documents.download(session, parsed["attachments"], folder)
        record["attachments"] = saved
        record["attachment_count"] = sum(1 for a in saved if a.get("file"))
        document_text = documents.extract_text(folder)
    else:
        record["attachments"] = []
        record["attachment_count"] = 0

    # Evaluation — the shared funnel, over the description, the NAICS, and
    # everything the page and its documents said.
    verdict = unison_evaluation.evaluate(
        {**record, "buy_description": record.get("buyer_description"),
         "general_info": general, "bidding_requirements": parsed["bidding_requirements"],
         "buy_terms": parsed["buy_terms"], "shipping": shipping,
         "buy_number": record.get("buyer_number")},
        document_text,
    )
    record["hint_evidence"] = verdict.pop("hint_evidence", "")
    record.update(verdict)
    return record


def execute_run(run_id: str) -> None:
    run_manager.update_run(run_id, status="running", step="scraping")
    _save_run_row(run_id)

    run_dir = run_manager.run_folder(run_id)
    records: list[dict[str, Any]] = []
    run = run_manager.get_run(run_id) or {}
    filter_id = str(run.get("filter_id") or filters.DEFAULT_FILTER_ID)
    scraper = None
    # Attachments are read for the decision and discarded with this directory.
    # Deliberately outside the run's workspace: a run delivers the spreadsheet,
    # and nothing that lands here can end up in it.
    docs_root = Path(tempfile.mkdtemp(prefix="unison_docs_"))
    try:
        _verify_credentials(run_id)
        run_manager.update_run(run_id, step="reading_listing")

        logger.info("[run %s] filters active: %s", run_id, filters.summary(filter_id))
        scraper = UnisonMarketplaceScraper()
        # Hidden by default; show the browser only for a live-preview run.
        scraper.headless = not run.get("live_preview", False)
        # The engine drops a request whose description matches one of these;
        # empty while the exclusions are off, so nothing is dropped.
        scraper.keywords_to_exclude = filters.excluded_keywords()
        live.register(run_id, scraper)  # shared live-screenshot endpoint

        # Pass 1 — the listing: Show 100, the chosen Filter By, every page. The
        # session is left open for the detail pass that follows.
        rows = scraper.open_listing(filter_id=filter_id, page_size=filters.PAGE_SIZE)
        records = _listing_records(rows)
        detected = scraper.expected_buys
        run_manager.update_run(
            run_id,
            bids_found=len(records),
            pages_scraped=scraper.pages_scraped,
            bids_detected=detected,
        )
        logger.info(
            "[run %s] listing: read %d of %s buy(s) detected, across %d page(s)",
            run_id, len(records), detected if detected is not None else "?",
            scraper.pages_scraped,
        )
        # The count the run is held to. A listing that reports 115 and yields 100
        # is not a smaller listing — it is fifteen buys nobody will ever see, and
        # it used to pass as a clean run because nothing compared the two numbers
        # out loud.
        if not getattr(scraper, "filter_applied", True):
            message = (
                f"the '{filters.filter_label(filter_id)}' criterion could not be "
                f"applied — this run read the unfiltered listing, so its counts "
                f"are against every open buy rather than that criterion's"
            )
            logger.error("[run %s] %s", run_id, message)
            run_manager.add_error(run_id, message)

        if detected is not None and len(records) < detected:
            message = (
                f"the portal reported {detected} buys but only {len(records)} were "
                f"read across {scraper.pages_scraped} page(s) — "
                f"{detected - len(records)} were not collected"
            )
            logger.error("[run %s] %s", run_id, message)
            run_manager.add_error(run_id, message)

        # Pass 2 — each buy's detail page, documents and verdict. One failure
        # costs that buy; the row stays in the report carrying its error.
        session = documents.session_from_driver(scraper.driver)
        for index, record in enumerate(records, start=1):
            run_manager.update_run(
                run_id,
                step=f"detail ({index}/{len(records)}): {record.get('buyer_number', '')}",
            )
            # Step 0: a buy whose listing row already names a GSA vehicle is out
            # before anything is fetched for it. Opening the page and pulling its
            # documents could not change the answer, so it is a page load and a
            # handful of PDFs saved on every hit. It stays in the report, with
            # the verdict and the reason, exactly as a screened buy always did.
            early = unison_evaluation.screen_listing(record)
            if early is not None:
                record.update(early)
                record["attachments"] = []
                record["attachment_count"] = 0
                record["line_items"] = []
                record["line_item_count"] = 0
                run_manager.update_run(run_id, bids_processed=index)
                continue
            # Checked per buy rather than only at the end: stopping kills the
            # browser, so without this every remaining buy would raise and be
            # filed as its own failure, burying the run's real errors under a
            # few hundred that only say "the user pressed Stop".
            if run_manager.is_stop_requested(run_id):
                logger.info(
                    "[run %s] stopped by user at buy %d of %d — keeping what was scraped",
                    run_id, index, len(records),
                )
                break
            try:
                _scrape_detail(scraper, session, record, docs_root)
            except Exception as exc:  # noqa: BLE001 — one buy must not sink the run
                record["error"] = str(exc)[:300]
                logger.exception("[run %s] %s failed", run_id, record.get("buyer_number"))
                run_manager.add_error(run_id, f"{record.get('buyer_number')}: {str(exc)[:200]}")
            run_manager.update_run(run_id, bids_processed=index)

        documents_downloaded = sum(int(r.get("attachment_count") or 0) for r in records)
        decisions: dict[str, int] = {}
        for record in records:
            decisions[record.get("decision") or "NOT_EVALUATED"] = (
                decisions.get(record.get("decision") or "NOT_EVALUATED", 0) + 1
            )
        # How many verdicts the strict fallback settled that would otherwise have
        # gone to a person, and how many the early-exit screens took off the
        # table before the funnel ran. Reported because both are the point of the
        # change and both are invisible from the decision tally alone: a REJECT
        # made by a screen and one made by Rule B read the same in that count.
        resolved = sum(1 for r in records if r.get("decision_before_strict"))
        screened = sum(1 for r in records if str(r.get("rule") or "").startswith("screen:"))
        run_manager.update_run(
            run_id,
            documents_downloaded=documents_downloaded,
            decisions=decisions,
            manual_review_resolved=resolved,
            screened_out=screened,
        )
        logger.info(
            "[run %s] decisions: %s | %d document(s) downloaded | %d rejected by "
            "an early-exit screen | %d resolved off the manual-review queue",
            run_id, decisions, documents_downloaded, screened, resolved,
        )
        if resolved:
            logger.info(
                "[run %s] strict fallback settled: %s",
                run_id,
                ", ".join(
                    f"{r.get('buyer_number')}={r.get('decision')}"
                    for r in records if r.get("decision_before_strict")
                )[:800],
            )

        # The close-date rule is off for testing, so this passes every record
        # through. The tallies are only published when it actually ran —
        # reporting a filter that did nothing would put a note in the console
        # claiming records were dropped.
        records, skipped_soon, unreadable_close, applied = filters.apply_close_date_filter(
            records, lambda r: r.get("end_date")
        )
        if applied:
            run_manager.update_run(
                run_id,
                min_days_until_close=MIN_DAYS_UNTIL_CLOSE,
                bids_skipped_closing_soon=skipped_soon,
                bids_kept_unreadable_close=unreadable_close,
            )
            logger.info(
                "[run %s] close-date filter (≥%sd): kept %s, skipped %s closing soon, %s unreadable kept",
                run_id, MIN_DAYS_UNTIL_CLOSE, len(records), skipped_soon, unreadable_close,
            )

        # The whole job in one line: what the portal said it had, what the walk
        # read, and what came out the far end of the detail pass. The three
        # agreeing is the guarantee this pipeline exists to make; any two of them
        # disagreeing names the stage that lost the bids.
        if detected:
            logger.info(
                "[PIPELINE COMPLETE]: Total Processed: %d / Total Detected: %d (%.0f%% Coverage)",
                len(records), detected, 100 * len(records) / detected,
            )
        else:
            logger.info(
                "[PIPELINE COMPLETE]: Total Processed: %d (the portal stated no total)",
                len(records),
            )
        run_manager.update_run(run_id, bids_found=len(records), bids_processed=len(records))
        # Every record, not the first hundred. The slice here was a display
        # limit and never touched processing — but a run of 136 buys showed 100
        # rows in the console, which is indistinguishable from the truncation
        # bug it sat next to. The ceiling below is a memory guard for a
        # pathological listing, an order of magnitude above any real one, and it
        # says so when it bites.
        if len(records) > LIVE_PREVIEW_CEILING:
            logger.warning(
                "[run %s] showing the first %d of %d records in the console; the "
                "spreadsheet and the database hold all of them",
                run_id, LIVE_PREVIEW_CEILING, len(records),
            )
        for rec in records[:LIVE_PREVIEW_CEILING]:
            run_manager.add_bid_result(run_id, {
                "buyer_number": rec.get("buyer_number"),
                "buyer_description": rec.get("buyer_description"),
                "buyer": rec.get("buyer"),
                "end_date": rec.get("end_date"),
                "decision": rec.get("decision"),
                "reason": rec.get("reason"),
                # The console shows how many files a buy carried, not the
                # evaluator's working-out.
                "documents": [a.get("file") for a in (rec.get("attachments") or []) if a.get("file")],
                "error": rec.get("error"),
            })
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
        if db_ok:
            # No Excel is written to disk any more — the sheet is rebuilt from
            # the DB on demand (Download button / completion email).
            run_manager.update_run(run_id, excel_exported=True)
        else:
            # DB outage: the records exist only in memory, so a disk Excel is
            # the only copy the download/email can serve.
            search = (run.get("search") or "all requests").strip()
            name = sanitize_filename(f"Unison_({search})", max_length=150)
            excel_path = _unique_path(run_dir / f"{name}.xlsx")
            try:
                export.generate_excel_from_records(records, excel_path)
                run_manager.update_run(run_id, excel_path=str(excel_path), excel_exported=True)
            except Exception:  # noqa: BLE001
                logger.exception("[run %s] Unison Excel generation failed", run_id)
                run_manager.add_error(run_id, "excel generation failed (see logs)")

        # Package the run into one archive ZIP (cumulative Excel + any files)
        # and delete the workspace — nothing stays on local disk.
        run_manager.update_run(run_id, step="packaging_results")
        archive_run(run_id)

        # A run the user stopped keeps the status Stop gave it, and records that
        # it nevertheless has rows to download. See run_manager.mark_partial.
        if run_manager.is_stop_requested(run_id):
            run_manager.mark_partial(run_id, len(records))
        else:
            run_manager.update_run(run_id, status="completed", step="done")
            # Email/S3 notification on successful completion.
            notify_scrape_completion(run_id, "unison", len(records))
    except Exception as exc:  # noqa: BLE001 — a failed run must be reported, not crash the worker
        logger.exception("[run %s] Unison run failed", run_id)
        run_manager.add_error(run_id, str(exc)[:500])
        run_manager.update_run(run_id, status="failed", step="failed")
    finally:
        live.unregister(run_id)
        # The engine leaves its browser open for the detail pass, so closing it
        # is this runner's job — including when the run failed part-way.
        shutil.rmtree(docs_root, ignore_errors=True)
        if scraper is not None and getattr(scraper, "driver", None) is not None:
            try:
                scraper.driver.quit()
            except Exception:  # noqa: BLE001 — the browser may already be gone
                logger.debug("[run %s] browser already closed", run_id, exc_info=True)
        run_manager.update_run(run_id, finished_at=datetime.now().isoformat())
        _save_run_row(run_id)
        run_manager.remove_empty_folder(run_id)


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
