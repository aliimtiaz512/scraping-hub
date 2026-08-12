"""The ad-status sweep: every advertisement matching a status, captured whole.

Subclasses `MFMPScraper` and overrides three things. Subclassing rather than
editing the parent is deliberate — the niche flow's code path stays what it was,
and portal fixes to login/search land in both flows:

  open_advanced_search  the sweep sets no commodity codes, so the parent's
                        accordion expansion is skipped.
  _capture_bid          reads `#mainSection` and keeps every attachment in the
                        bid's own folder.
  run                   pagination, capture, and the packaged delivery.

Pagination is the substantive addition. The parent assumes a single page,
which holds for a code/keyword search but not for an unfiltered status sweep.
The portal's Angular Material paginator is walked until its Next button is
disabled, following the shape already proven in the Wisconsin scraper.

**Nothing is scored, categorised or dropped.** The sweep used to run each
advertisement through a C/T/S evaluation matrix, file it into a niche lane, and
delete its attachments once their text had fed the score — so what reached the
reviewer was the classifier's opinion of the portal, and the documents behind
that opinion were already gone. Every ad the search returns is now captured as
it stands, with its attachments kept in `Bids_Data/<ad number>/` and one summary
sheet indexing the lot; deciding what is worth pursuing is the reviewer's.

The classifier itself (`scoring.py`, `routing.py`, `matching.py`,
`mfmp_niches.yaml`) is still in the tree and still serves the lane views and
workbooks of sweep runs recorded *before* this change. Nothing in this flow
calls it.
"""

from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

from app.config import settings
from app.core import run_manager
from app.core.base_scraper import StopRequested
from app.core.exports import archive_run
from app.scrapers.myflorida import storage
from app.scrapers.myflorida.ingest import map_row, parse_excel
from app.scrapers.myflorida.scraper import MFMPScraper, describe_error
from app.scrapers.myflorida.sweep import export
from app.scrapers.myflorida.workbook import build_from_records, merge_exports
from app.services.notifier import notify_scrape_completion

logger = logging.getLogger(__name__)

SEL = {
    # Anchored on aria-label and the Material class. Never on _ngcontent-*,
    # which Angular regenerates on every portal build.
    "paginator_next": (By.CSS_SELECTOR, "button.mat-paginator-navigation-next"),
    "paginator_previous": (By.CSS_SELECTOR, "button.mat-paginator-navigation-previous"),
    "paginator_first": (By.CSS_SELECTOR, "button.mat-paginator-navigation-first"),
    "paginator_range": (By.CSS_SELECTOR, ".mat-paginator-range-label"),
    # The ad body. A stable element id, unlike everything around it.
    "description": (By.ID, "mainSection"),
}

# Stops a runaway loop if the Next button never reports itself disabled.
MAX_PAGES = 200
PAGE_SETTLE_SECONDS = 1.5

# The grid renders a whole page of rows at once, so a bid's link is either in
# the DOM as soon as the page settles or not on this page at all. Waiting the
# default 30s for it only multiplies the cost of a paginator that has drifted —
# a hundred absent links is fifty minutes at that timeout, eight at this one.
LINK_TIMEOUT = 8


class SweepScraper(MFMPScraper):
    def __init__(self, run_id: str, ad_statuses: list[str], max_bids: int | None = None):
        super().__init__(run_id, codes=[], ad_statuses=ad_statuses, ad_types=[], keywords=[])
        self.max_bids = max_bids
        self.exports: list[Path] = []
        self.excel_path: Path | None = None

    # -- overrides ----------------------------------------------------------

    def open_advanced_search(self) -> None:
        """Advanced Search with the result cap raised and no commodity control.

        The parent expands the Commodity Codes accordion whenever the run is not
        in keyword mode; the sweep filters on Ad Status alone, so that step is
        dropped rather than the parent being changed.
        """
        self.set_step("opening_advanced_search")
        self._robust_click(self._sel("advanced_search_button"))
        self._set_max_results("100")

    def _sel(self, key: str):
        from app.scrapers.myflorida.scraper import SEL as PARENT_SEL

        return PARENT_SEL[key]

    # -- pagination ---------------------------------------------------------

    def _range_label(self) -> str:
        try:
            return self.driver.find_element(*SEL["paginator_range"]).text.strip()
        except WebDriverException:
            return ""

    def _paginator_enabled(self, key: str) -> bool:
        """True when the named paginator arrow exists and is live. Material
        disables the arrow at the end of its direction, which is the page loops'
        terminating condition — no guessing."""
        buttons = self.driver.find_elements(*SEL[key])
        if not buttons:
            return False  # no paginator at all: a single page of results
        try:
            if buttons[0].get_attribute("disabled") is not None:
                return False
            return "mat-button-disabled" not in (buttons[0].get_attribute("class") or "")
        except WebDriverException:
            return False

    def _next_enabled(self) -> bool:
        return self._paginator_enabled("paginator_next")

    def _first_ad_number(self) -> str:
        """The first row's ad number — the marker used to tell whether the grid
        is still showing the page we left. Cheaper and more reliable than the
        range label, which may not be rendered at all."""
        for row in self.driver.find_elements(*self._sel("results_rows")):
            try:
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) >= 2:
                    links = cells[1].find_elements(By.TAG_NAME, "a")
                    if links:
                        return links[0].text.strip()
            except WebDriverException:
                continue
        return ""

    def _step_page(self, key: str) -> bool:
        """Click one paginator arrow and wait for the grid to actually redraw."""
        before = self._first_ad_number()
        try:
            self._robust_click(SEL[key])
        except (TimeoutException, WebDriverException):
            return False
        # The grid re-renders in place, so "the page changed" is the first row
        # changing rather than any navigation event.
        try:
            self.wait(20).until(lambda _d: self._first_ad_number() not in ("", before))
        except TimeoutException:
            return False
        time.sleep(PAGE_SETTLE_SECONDS)
        return True

    def _go_next_page(self) -> bool:
        return self._step_page("paginator_next")

    def _goto_first_page(self) -> bool:
        """Send the grid back to page 1.

        The first-page button only renders when the portal enables Material's
        showFirstLastButtons, so its absence is an ordinary case and not an
        error — checked with find_elements rather than a wait, because
        `_robust_click` would otherwise spend three 30s waits discovering that a
        button which is never coming is still not there. Walking backwards one
        page at a time is the fallback every paginator supports.
        """
        if self.driver.find_elements(*SEL["paginator_first"]):
            try:
                self._robust_click(SEL["paginator_first"])
                time.sleep(PAGE_SETTLE_SECONDS)
                return True
            except (TimeoutException, WebDriverException):
                pass
        for _ in range(MAX_PAGES):
            if not self._paginator_enabled("paginator_previous"):
                return True  # the Previous arrow is dead: this is page 1
            if not self._step_page("paginator_previous"):
                return False
        return False

    def _restore_page(self, page_index: int, expected_first: str) -> bool:
        """Make sure the grid is showing the page a bid was collected from.

        Two things put it elsewhere. Material keeps its page index in component
        memory rather than the URL, so returning from a detail page can silently
        drop the grid back to page 1; and collection leaves the grid on the
        *last* page, which is where evaluation would otherwise start hunting for
        page 1's bids. Either way the links are simply absent, so the bids are
        skipped one 30s timeout at a time — the failure is slow, not loud.

        Cheap when the grid is already right (a single comparison), correct when
        it is not.
        """
        if self._first_ad_number() == expected_first:
            return True
        logger.info("[run %s] paginator drifted — restoring page %d",
                    self.run_id, page_index + 1)
        if not self._goto_first_page():
            return False
        for _ in range(page_index):
            if not self._go_next_page():
                return False
            if self._first_ad_number() == expected_first:
                return True
        return self._first_ad_number() == expected_first

    # -- per-bid ------------------------------------------------------------

    def _read_description(self) -> str:
        """The ad body from `#mainSection`.

        Read as rendered text: the markup nests <p> inside <p>, which is invalid
        HTML, so the parsed DOM does not match the source and innerText is the
        only reading that survives the browser's re-parse. It also turns &nbsp;
        into ordinary spaces.
        """
        try:
            element = self.wait(10).until(EC.presence_of_element_located(SEL["description"]))
            return (element.text or "").strip()
        except (TimeoutException, WebDriverException):
            run_manager.add_warning(self.run_id, "description (#mainSection) not found on a bid")
            return ""

    def _capture_bid(self, bid: dict) -> dict[str, Any]:
        """Open a bid, read it, keep every attachment, come back.

        The attachments are the change. They used to land in a scratch folder,
        have their text lifted for the classifier, and be deleted before the next
        bid — the sweep's contract was one spreadsheet and nothing else. They now
        go straight into this bid's own folder inside the export tree
        (`Bids_Data/<ad number>/`) and stay there, because a reviewer reading a
        row is exactly the person who needs the solicitation PDF next to it.

        A document that fails to download is recorded against the bid rather
        than raised: a bid with three of four attachments is still worth having,
        and the missing one is named in the summary sheet.
        """
        number = bid["number"]
        self.set_step(f"capturing:{number}")

        link = self.wait(LINK_TIMEOUT).until(EC.element_to_be_clickable((By.LINK_TEXT, number)))
        self.scroll_into_view(link)
        link.click()
        self.wait().until(lambda d: "/detail/" in d.current_url)
        time.sleep(2)  # let the detail page (incl. the document list) render

        detail_url = self.driver.current_url
        description = self._read_description()
        title = bid.get("title", "")

        folder = storage.bid_folder(self.run_dir, number, title)
        saved: list[str] = []
        errors: list[str] = []
        for index, doc_link in enumerate(self.driver.find_elements(*self._sel("document_links")), 1):
            try:
                self.scroll_into_view(doc_link)
                self.driver.execute_script("arguments[0].click();", doc_link)
                downloaded = self.wait_for_download()
                # Keep the portal's own filename; only prefix an index when two
                # attachments on the same bid share one.
                target = folder / downloaded.name
                if target.exists():
                    target = folder / f"{index}_{downloaded.name}"
                shutil.move(str(downloaded), str(target))
                saved.append(target.name)
            except (TimeoutException, WebDriverException, OSError) as exc:
                errors.append(f"doc {index}: {exc.__class__.__name__}")

        self.driver.back()
        self.wait().until(EC.presence_of_element_located(self._sel("results_rows")))
        time.sleep(1)

        return {
            "ad_number": number,
            "title": title,
            "description": description,
            "detail_url": detail_url,
            "documents": saved,
            "folder": storage.folder_reference(number, title),
            "document_errors": errors,
        }

    # -- orchestration ------------------------------------------------------

    def _collect_all_pages(self) -> list[dict]:
        """Walk every page, exporting each one's metadata as we go.

        The export is taken per page because whether the portal's Export button
        covers the whole result set or only what is displayed is unverified; a
        per-page export is correct either way, and `merge_exports` already
        de-duplicates by ad number.
        """
        collected: list[dict] = []
        seen: set[str] = set()

        for page in range(MAX_PAGES):
            self.set_step(f"collecting_page:{page + 1}")
            rows = self.collect_bids()
            new = [r for r in rows if r["number"] not in seen]
            for row in new:
                seen.add(row["number"])
                row["page"] = page
            collected.extend(new)

            label = self._range_label()
            run_manager.update_run(
                self.run_id,
                bids_found=len(collected),
                page=page + 1,
                page_range=label or None,
            )
            logger.info("[run %s] page %d: %d rows (%d total) %s",
                        self.run_id, page + 1, len(new), len(collected), label)

            export_path = self._export(f"page{page + 1}")
            if export_path:
                self.exports.append(export_path)

            if self.max_bids and len(collected) >= self.max_bids:
                run_manager.add_warning(
                    self.run_id, f"stopped collecting at the {self.max_bids}-bid cap")
                break
            if not self._next_enabled():
                break
            if not self._go_next_page():
                run_manager.add_warning(
                    self.run_id, f"could not advance past page {page + 1}")
                break
        else:
            run_manager.add_error(self.run_id, f"stopped at the page cap ({MAX_PAGES})")

        if self.max_bids:
            collected = collected[: self.max_bids]
        return collected

    def _metadata_by_ad(self) -> dict[str, dict[str, Any]]:
        """Portal export columns keyed by ad number.

        The results grid carries only number and title; agency, type, dates and
        any commodity-code column come from the export. Reuses the niche flow's
        `map_row` so "which column is the ad number" stays in one place.
        """
        out: dict[str, dict[str, Any]] = {}
        for path in self.exports:
            try:
                for raw in parse_excel(path):
                    mapped = map_row(raw)
                    ad = (mapped.get("ad_number") or "").strip()
                    if ad and ad not in out:
                        out[ad] = {**mapped, "raw_data": raw}
            except Exception:  # noqa: BLE001 — a bad export must not fail the run
                logger.exception("[run %s] could not read export %s", self.run_id, path)
        return out

    def _record(self, captured: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        """One advertisement as it will be stored and summarised.

        The portal's own export columns (agency, type, status, dates) merged with
        what the detail page gave (description, attachments, the folder they are
        in). Nothing is interpreted: no score, no niche, no verdict — the row is
        what the portal said, plus where the files went.
        """
        return {
            "ad_number": captured["ad_number"],
            "title": captured.get("title") or meta.get("title"),
            "agency": meta.get("agency"),
            "ad_type": meta.get("ad_type"),
            "status": meta.get("status"),
            "ad_date": meta.get("ad_date"),
            "open_date": meta.get("open_date"),
            "close_date": meta.get("close_date"),
            "description": captured.get("description"),
            "detail_url": captured.get("detail_url"),
            "documents": captured.get("documents") or [],
            "folder": captured.get("folder"),
            "document_errors": captured.get("document_errors") or [],
            "raw_data": meta.get("raw_data"),
        }

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        records: list[dict[str, Any]] = []
        try:
            # Visible for the same reason the niche flow is: the login stops for
            # a one-time password that a person has to type in.
            self.start_driver(headless=False if settings.mfmp_manual_otp else None)
            self.login()
            self.open_advertisements()
            self.open_advanced_search()
            self.select_ad_status()
            outcome = self.submit_search()

            if outcome != self.RESULTS:
                run_manager.add_warning(self.run_id, "the search returned no advertisements")
                run_manager.update_run(self.run_id, no_results=True)
            else:
                bids = self._collect_all_pages()
                metadata = self._metadata_by_ad()
                records = self._process(bids, metadata)

            self._finalize(records)
            run_manager.update_run(self.run_id, status="completed", step="done")
            notify_scrape_completion(self.run_id, "myflorida_sweep", len(records))
        except StopRequested:
            # Everything captured so far is still packaged: the attachments are
            # already on disk and the reviewer should get what the run got.
            logger.info("[run %s] stopped by user", self.run_id)
            self._finalize(records)
            run_manager.update_run(self.run_id, status="completed", step="stopped")
        except Exception as exc:  # noqa: BLE001 — a failed run must be reported, not crash the worker
            logger.exception("[run %s] sweep failed", self.run_id)
            self.screenshot("fatal")
            run_manager.add_error(self.run_id, describe_error(exc, self.current_step))
            run_manager.update_run(self.run_id, status="failed", step="failed")
        finally:
            self.cleanup()
            run_manager.update_run(self.run_id, finished_at=datetime.now().isoformat())
            run_manager.remove_empty_folder(self.run_id)

    def _process(
        self, bids: list[dict], metadata: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Open every collected bid and keep it, page by page. No bid is skipped
        on the strength of anything read from it."""
        records: list[dict[str, Any]] = []
        by_page: dict[int, list[dict]] = {}
        for bid in bids:
            by_page.setdefault(bid.get("page", 0), []).append(bid)

        for page_index in sorted(by_page):
            page_bids = by_page[page_index]
            expected_first = page_bids[0]["number"]

            for offset, bid in enumerate(page_bids):
                # Before every bid, not only at the top of the page: a bid that
                # fails skips straight to the next one, so a restore that only
                # ran after a success would never run again once the grid had
                # drifted. A no-op whenever the grid is already right.
                if not self._restore_page(page_index, expected_first):
                    run_manager.add_error(
                        self.run_id,
                        f"could not return to page {page_index + 1} — "
                        f"{len(page_bids) - offset} advertisement(s) not captured",
                    )
                    break
                try:
                    captured = self._capture_bid(bid)
                except StopRequested:
                    raise
                except (TimeoutException, WebDriverException) as exc:
                    run_manager.add_error(
                        self.run_id, f"bid {bid['number']}: {exc.__class__.__name__}")
                    self.screenshot(f"bid_{bid['number']}")
                    self._recover_to_results()
                    continue

                record = self._record(captured, metadata.get(bid["number"], {}))
                records.append(record)
                run_manager.update_run(
                    self.run_id,
                    bids_processed=len(records),
                    documents_downloaded=sum(len(r["documents"]) for r in records),
                    bids=[{
                        "number": r["ad_number"],
                        "title": r.get("title"),
                        # A count, not the list: `documents` on a result row
                        # means the filenames themselves everywhere else, and
                        # the live table only needs how many there were.
                        "document_count": len(r["documents"]),
                        "folder": r.get("folder"),
                    } for r in records[-20:]],
                )
        return records

    def _finalize(self, records: list[dict[str, Any]]) -> None:
        """Summary sheet, database, archive. Each step best-effort.

        The summary is written **to disk first and always**, unlike the old
        delivery which leaned on the DB to rebuild the workbook on demand. It has
        to be: the deliverable is now a ZIP whose root holds the sheet next to
        the documents it indexes, so the file has to exist at package time
        whatever the database is doing.
        """
        if not records:
            run_manager.update_run(self.run_id, no_results=True)

        self.set_step("merging_workbook")
        # The portal's own per-page exports, stitched into one sheet, with each
        # row pointing at the folder holding that ad's documents. Falls back to
        # building the sheet from what was captured if the portal's exports are
        # unusable — a summary is not optional, it is the archive's index.
        folder_by_ad = {r["ad_number"]: r.get("folder", "") for r in records}
        try:
            if self.exports:
                self.excel_path = merge_exports(
                    self.exports, self.run_dir, self._niche_label(), {}, folder_by_ad
                )
            else:
                self.excel_path = build_from_records(records, self.run_dir)
        except Exception:  # noqa: BLE001 — never fail a run over the workbook
            logger.exception("[run %s] summary sheet failed; rebuilding from records",
                             self.run_id)
            try:
                self.excel_path = build_from_records(records, self.run_dir)
            except Exception:  # noqa: BLE001
                logger.exception("[run %s] summary rebuild failed too", self.run_id)
                run_manager.add_error(self.run_id, "summary sheet could not be written")
        if self.excel_path:
            run_manager.update_run(
                self.run_id, excel_path=str(self.excel_path), excel_exported=True
            )

        self.set_step("storing_in_db")
        try:
            stored = export.save_capture(self.run_id, records)
            run_manager.update_run(self.run_id, bids_stored_in_db=stored)
        except Exception as exc:  # noqa: BLE001 — the files on disk are the record
            logger.exception("[run %s] sweep DB save failed", self.run_id)
            run_manager.add_error(self.run_id, f"db save failed: {exc.__class__.__name__}")
            # Tells the download/email path to serve the sheet on disk rather
            # than regenerate an empty one from a DB that never got the rows.
            run_manager.update_run(self.run_id, db_save_failed=True)

        self.set_step("packaging_results")
        archive_run(self.run_id)


def execute_run(run_id: str, ad_statuses: list[str], max_bids: int | None = None) -> None:
    SweepScraper(run_id, ad_statuses, max_bids).run()
