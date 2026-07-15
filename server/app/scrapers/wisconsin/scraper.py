"""Selenium automation for the Wisconsin eSupplier (PeopleSoft) bidder portal.

Flow: open the eSupplier landing page -> click the "Wisconsin Bidder Portal"
tile -> click the "Current Solicitations" tile -> fill the search criteria
(keyword / agency / NIGP code, all optional) -> Search -> page through the whole
results grid, storing every row (Event Number, Solicitation Reference #, Event
Type, Event Title, Agency, Event Status, Due Date/Time) in the DB -> generate an
Excel (Wisconsin_<date>_<time>.xlsx) from the DB into the run folder.

The site is Oracle PeopleSoft: tiles open via LaunchTileURL (often a new
window), and the results grid paginates through classic PeopleSoft postbacks
(submitAction_win0) with a "X-Y of Total" range indicator.
"""

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

from app.config import settings
from app.core import run_manager
from app.core.base_scraper import BaseScraper
from app.scrapers.wisconsin import export

logger = logging.getLogger(__name__)

# Search-criteria inputs and button (ids are stable PeopleSoft field names).
KEYWORD_ID = "WI_SS_BIDR_SRCH_WI_SS_SRCH_KEYWORD"
AGENCY_ID = "WI_SS_BIDR_SRCH_WI_SS_SRCH_AGENCY"
NIGP_ID = "WI_SS_BIDR_SRCH_CATEGORY_CD"
SEARCH_BTN_ID = "WI_SS_BIDR_SRCH_SEARCH"

# Results grid: data rows, the page-range dropdown (read-only, for the "X-Y of
# Total" indicator), and the "show next row" anchor that advances one page. The
# anchor's href is javascript:submitAction_win0(...); activating it posts back
# reliably, whereas calling submitAction directly from execute_script does not.
ROW_CSS = "tr[id^='trWI_SS_BIDALL_VW']"
PAGE_SELECT_ID = "WI_SS_BIDALL_VW$hpage$0"
NEXT_ID = "WI_SS_BIDALL_VW$hdown$0"

PREVIEW_LIMIT = 100   # rows mirrored to the live run state for the UI table
MAX_PAGES = 300       # pagination safety guard (25 rows/page => up to 7500 rows)


def _xpath_literal(text: str) -> str:
    """Quote a string for use inside an XPath expression."""
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    parts = text.split("'")
    return "concat('" + "', \"'\", '".join(parts) + "')"


class WisconsinScraper(BaseScraper):
    def __init__(self, run_id: str, keyword: str = "", agency: str = "", nigp_code: str = ""):
        super().__init__(run_id)
        self.keyword = keyword.strip()
        self.agency = agency.strip()
        self.nigp_code = nigp_code.strip()
        self.excel_path: Path | None = None
        # Full in-memory copy of every scraped row, used only as a fallback source
        # for the Excel if the DB is unavailable.
        self._records: list[dict[str, Any]] = []

    # -- navigation ---------------------------------------------------------

    def _find_in_window(self, locator: tuple) -> bool:
        """Return True (leaving the driver focused there) if `locator` is present
        in the current window at the top level or inside a content iframe.

        PeopleSoft classic components (the search form, the results grid) render
        inside the target-content iframe (ptifrmtgtframe), while the Fluid tiles
        live at the top level — so we check both.
        """
        try:
            self.driver.switch_to.default_content()
            if self.driver.find_elements(*locator):
                return True
        except WebDriverException:
            pass
        frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe[id^='ptifrm'], iframe[name='TargetContent']")
        if not frames:
            try:
                frames = self.driver.find_elements(By.TAG_NAME, "iframe")
            except WebDriverException:
                frames = []
        for frame in frames:
            try:
                self.driver.switch_to.default_content()
                self.driver.switch_to.frame(frame)
                if self.driver.find_elements(*locator):
                    return True
            except WebDriverException:
                continue
        return False

    def _focus_target(self, locator: tuple, timeout: int = 40) -> bool:
        """Wait until `locator` is present in some window/frame and focus there.

        A tile can open its target in a new window and inside the content iframe,
        so we sweep every window handle (and each one's frames) until we find it.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for handle in self.driver.window_handles:
                try:
                    self.driver.switch_to.window(handle)
                except WebDriverException:
                    continue
                if self._find_in_window(locator):
                    return True
            time.sleep(0.5)
        return False

    def _open_tile(self, label: str, expected: tuple) -> None:
        """Click a PeopleSoft tile by its visible label and focus its target.

        LaunchTileURL often opens the target in a new window whose classic content
        lives inside an iframe, so we resolve the target across windows/frames.
        """
        tile = self.wait(30).until(EC.element_to_be_clickable(
            (By.XPATH, f"//div[@role='link'][.//span[normalize-space()={_xpath_literal(label)}]]")
        ))
        self.scroll_into_view(tile)
        tile.click()
        time.sleep(2)
        if not self._focus_target(expected):
            raise TimeoutException(f"target not found after opening tile '{label}'")

    def open_current_solicitations(self) -> None:
        self.set_step("opening_portal")
        self.driver.get(settings.wisconsin_url)
        # Landing page -> Wisconsin Bidder Portal tile (target page shows more tiles).
        self._open_tile(
            "Wisconsin Bidder Portal",
            (By.XPATH, "//div[@role='link'][.//span[normalize-space()='Current Solicitations']]"),
        )
        self.set_step("opening_current_solicitations")
        # Bidder Portal -> Current Solicitations tile (target page has the search form).
        self._open_tile("Current Solicitations", (By.ID, KEYWORD_ID))

    # -- search -------------------------------------------------------------

    def search(self) -> None:
        """Fill the criteria and submit via a native Search click.

        The Agency box is a server-side EXACT-match filter (verified against the
        live portal: "Department of Military Affairs" -> 138 rows; the partial
        "Military Affairs" -> 0 rows; blank -> every agency), so we type the name
        and let the portal return a grid already scoped to that agency. Only a
        native WebDriver click actually triggers the search postback here.
        """
        self.set_step("searching")
        # The form lives in the content iframe; make sure we're focused there.
        self._focus_target((By.ID, SEARCH_BTN_ID))
        if self.keyword:
            self._fill(KEYWORD_ID, self.keyword)
        if self.agency:
            self._fill(AGENCY_ID, self.agency)
        if self.nigp_code:
            self._fill(NIGP_ID, self.nigp_code)
        # Log the values actually sitting in the fields right before we submit —
        # if a criterion didn't register, the grid comes back unfiltered.
        try:
            vals = self.driver.execute_script(
                "return Array.prototype.slice.call(arguments).map("
                "  id => { var e = document.getElementById(id); return e ? e.value : null; });",
                KEYWORD_ID, AGENCY_ID, NIGP_ID,
            )
            logger.info("[run %s] submitting search: keyword=%r agency=%r nigp=%r",
                        self.run_id, *(vals or [None, None, None]))
        except WebDriverException:
            pass
        self.driver.find_element(By.ID, SEARCH_BTN_ID).click()
        # PeopleSoft runs the search as an AJAX postback behind a "processing"
        # overlay while the old (pre-search) grid is still on screen; wait for it
        # to finish so we don't scrape the stale grid. A search may legitimately
        # return no rows (e.g. an agency the portal can't resolve), so don't
        # hard-fail if none appear.
        self._wait_search_settled()
        if not self._focus_target((By.CSS_SELECTOR, ROW_CSS)):
            logger.info("[run %s] no result rows appeared after search", self.run_id)

    def _fill(self, elem_id: str, value: str) -> None:
        el = self.wait().until(EC.presence_of_element_located((By.ID, elem_id)))
        el.clear()
        el.send_keys(value)

    def _is_processing(self) -> bool:
        """Whether PeopleSoft's 'processing' overlay is currently visible (a
        postback/AJAX update is in flight and the grid is not yet settled)."""
        try:
            return bool(self.driver.execute_script(
                "var e = document.getElementById('processing');"
                "if (!e) return false;"
                "var s = window.getComputedStyle(e);"
                "return ((e.offsetWidth || e.offsetHeight) > 0)"
                "  && s.display !== 'none' && s.visibility !== 'hidden';"
            ))
        except WebDriverException:
            return False

    def _wait_search_settled(self, timeout: int = 40) -> None:
        """Block until the search postback finishes so we read the filtered grid,
        not the stale pre-search one. The overlay appears within a moment of the
        click and clears when results render; if it never shows within a short
        grace we assume there was nothing to wait for and proceed.
        """
        deadline = time.monotonic() + timeout
        grace = time.monotonic() + 3
        seen_busy = False
        while time.monotonic() < deadline:
            self._focus_target((By.CSS_SELECTOR, ROW_CSS), timeout=5)
            if self._is_processing():
                seen_busy = True
            elif seen_busy:
                logger.info("[run %s] search postback settled", self.run_id)
                return
            elif time.monotonic() > grace:
                logger.info("[run %s] search: no processing overlay observed; proceeding",
                            self.run_id)
                return
            time.sleep(0.2)

    # -- results ------------------------------------------------------------

    def scrape_all_pages(self) -> None:
        """Page through the entire results grid, saving each row as it's read.

        Classic PeopleSoft rebuilds the grid iframe on every postback, so we never
        hold Selenium element handles across renders: each page is read in a single
        atomic execute_script, and the iframe is re-focused at the top of each page.
        """
        self.set_step("scraping_results")
        preview: list[dict[str, Any]] = []
        seen: set[str] = set()
        saved = 0
        pages = 0
        last_end = 0
        while True:
            # Wait until the grid is fully rendered before reading it: match the
            # rendered row count to the range indicator's "X-Y of Total" so a
            # slow paint can't be read as a short final page and stop the run.
            rendered = self._wait_for_grid(timeout=40 if pages == 0 else 25)
            if rendered == 0:
                break
            pages += 1
            for rec in self._scrape_page():
                # Guard against reading the same row twice during a grid transition.
                key = rec.get("event_number") or rec.get("solicitation_reference")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                self._records.append(rec)
                try:
                    export.save_bid(self.run_id, rec)
                    saved += 1
                except Exception:  # noqa: BLE001 — DB issues shouldn't abort the run
                    logger.exception("[run %s] save_bid failed", self.run_id)
                    run_manager.add_error(self.run_id, "db save failed for a solicitation")
                if len(preview) < PREVIEW_LIMIT:
                    preview.append(self._display(rec))
            run_manager.update_run(
                self.run_id, bids_found=saved, bids_processed=saved, bids=list(preview)
            )
            logger.info("[run %s] page %s scraped (total saved %s)", self.run_id, pages, saved)

            end, total = self._page_indicator()
            logger.info("[run %s] page %s range indicator: end=%s total=%s last_end=%s",
                        self.run_id, pages, end, total, last_end)
            if end is None or total is None or end >= total or end <= last_end:
                logger.info("[run %s] pagination stop: no further range to fetch", self.run_id)
                break
            last_end = end
            if pages >= MAX_PAGES:
                run_manager.add_error(self.run_id, f"stopped at page cap ({MAX_PAGES})")
                break
            if not self._go_next_page():
                logger.info("[run %s] pagination stop: could not advance past page %s", self.run_id, pages)
                break

    # A single atomic read of the current grid page — returns a list of 7-string
    # rows — so no per-cell element handle can go stale mid-scrape.
    _JS_SCRAPE = """
        const norm = (el) => ((el ? el.textContent : '') || '').replace(/\\s+/g, ' ').trim();
        const out = [];
        for (const r of document.querySelectorAll("tr[id^='trWI_SS_BIDALL_VW']")) {
          const tds = r.querySelectorAll('td');
          if (tds.length < 7) continue;
          const cell = (td) => norm(td.querySelector('span') || td);
          out.push([cell(tds[0]), cell(tds[1]), cell(tds[2]), cell(tds[3]),
                    cell(tds[4]), cell(tds[5]), cell(tds[6])]);
        }
        return out;
    """

    # One atomic read of the grid's current state: how many data rows are
    # rendered and the page-range dropdown's selected text ("X-Y of Total").
    _JS_GRID_INFO = """
        const rows = document.querySelectorAll("tr[id^='trWI_SS_BIDALL_VW']").length;
        const e = document.getElementById(arguments[0]);
        let pager = null;
        if (e) {
          if (e.tagName === 'SELECT') { const o = e.options[e.selectedIndex]; pager = o ? o.text : null; }
          else pager = e.textContent;
        }
        const p = document.getElementById('processing');
        let busy = false;
        if (p) { const s = window.getComputedStyle(p);
                 busy = ((p.offsetWidth || p.offsetHeight) > 0)
                        && s.display !== 'none' && s.visibility !== 'hidden'; }
        return {rows: rows, pager: pager, busy: busy};
    """

    def _wait_for_grid(self, timeout: int) -> int:
        """Focus the grid iframe and wait until the page is fully rendered.

        Returns the number of data rows rendered (0 if the grid never appeared).

        When the range indicator is present we know exactly how many rows this
        page should have (Y - X + 1) and wait for that many to paint; without a
        pager (a single page of results) we wait for the row count to settle.
        """
        deadline = time.monotonic() + timeout
        prev = 0
        stable = 0
        while time.monotonic() < deadline:
            if not self._focus_target((By.CSS_SELECTOR, ROW_CSS), timeout=5):
                continue
            try:
                info = self.driver.execute_script(self._JS_GRID_INFO, PAGE_SELECT_ID)
            except WebDriverException:
                info = None
            if (info or {}).get("busy"):
                # A postback is still repainting the grid — don't read it yet.
                stable = 0
                time.sleep(0.6)
                continue
            rows = int((info or {}).get("rows") or 0)
            pager = (info or {}).get("pager")
            expected = None
            if pager:
                m = re.search(r"(\d+)\s*-\s*(\d+)\s+of\s+([\d,]+)", pager)
                if m:
                    expected = int(m.group(2)) - int(m.group(1)) + 1
            if expected is not None:
                if rows >= expected:
                    return rows
            elif rows > 0 and rows == prev:
                stable += 1
                if stable >= 2:
                    return rows
            else:
                stable = 0
            prev = rows
            time.sleep(0.6)
        if prev:
            logger.warning("[run %s] grid render wait timed out with %s rows", self.run_id, prev)
        return prev

    def _scrape_page(self) -> list[dict[str, Any]]:
        try:
            data = self.driver.execute_script(self._JS_SCRAPE)
        except WebDriverException:
            return []
        records: list[dict[str, Any]] = []
        for row in data or []:
            rec = {
                "event_number": row[0],
                "solicitation_reference": row[1],
                "event_type": row[2],
                "event_title": row[3],
                "agency": row[4],
                "event_status": row[5],
                "due_datetime": row[6],
            }
            if rec["event_number"] or rec["solicitation_reference"]:
                records.append(rec)
        return records

    @staticmethod
    def _display(rec: dict[str, Any]) -> dict[str, Any]:
        """A per-row dict for the live run state / frontend results table."""
        return {**rec, "documents": [], "error": None}

    def _page_indicator(self) -> tuple[int | None, int | None]:
        """Read the grid's "X-Y of Total" range dropdown → (Y, Total) via JS.

        Returns (None, None) when there is no pager (a single page of results).
        """
        try:
            text = self.driver.execute_script(
                "var e = document.getElementById(arguments[0]);"
                "if (!e) return null;"
                "if (e.tagName === 'SELECT') { var o = e.options[e.selectedIndex]; return o ? o.text : null; }"
                "return e.textContent;",
                PAGE_SELECT_ID,
            )
        except WebDriverException:
            return None, None
        if not text:
            return None, None
        match = re.search(r"(\d+)\s*-\s*(\d+)\s+of\s+([\d,]+)", text)
        if not match:
            return None, None
        return int(match.group(2)), int(match.group(3).replace(",", ""))

    def _first_event_number(self) -> str | None:
        """The first row's Event Number — a token to detect a grid refresh."""
        try:
            return self.driver.execute_script(
                "var td = document.querySelector(\"tr[id^='trWI_SS_BIDALL_VW'] td\");"
                "return td ? (td.textContent || '').trim() : null;"
            )
        except WebDriverException:
            return None

    def _await_turnover(self, before: str | None, timeout: int) -> str | None:
        """Re-focus the rebuilt grid iframe and wait until its first row's Event
        Number differs from `before`. Returns the new first Event Number on
        success (the grid turned over to a new page), else None.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.8)
            if not self._focus_target((By.CSS_SELECTOR, ROW_CSS), timeout=5):
                continue
            first = self._first_event_number()
            if first and first != before:
                return first
        return None

    def _go_next_page(self) -> bool:
        """Advance one page using the grid's "next row" arrow (WI_SS_BIDALL_VW$hdown$0).

        The arrow is an <a> whose href is javascript:submitAction_win0(...). We
        try two activations, each confirmed by the grid *body* turning over (the
        first row's Event Number changing — the range indicator can update a beat
        before the body does, which would otherwise let us re-read the old page):

        1. A real native WebDriver click, which best emulates the user clicking
           the arrow. If the element isn't actionable (it is tabindex="-1" and
           wraps an <img>) the click raises and we move on without advancing.
        2. Failing that, invoke the anchor's own click() so the browser evaluates
           its javascript: href in the page's real context — this posts back even
           when calling submitAction directly from execute_script does not.

        We only fall through to (2) after (1) clearly did not turn the grid over,
        so we never skip a page by advancing twice.
        """
        before = self._first_event_number()

        # (1) native click
        try:
            nxt = self.wait(10).until(EC.element_to_be_clickable((By.ID, NEXT_ID)))
            self.scroll_into_view(nxt)
            nxt.click()
            first = self._await_turnover(before, timeout=40)
            if first:
                logger.info("[run %s] next-page (native click): turned over to first=%s",
                            self.run_id, first)
                return True
            logger.info("[run %s] next-page: native click did not turn the grid over; "
                        "trying anchor href", self.run_id)
        except WebDriverException as exc:
            logger.info("[run %s] next-page: native click unavailable (%s); trying anchor href",
                        self.run_id, type(exc).__name__)

        # (2) fire the anchor's javascript: href via its own click()
        try:
            self.driver.execute_script(
                "var a = document.getElementById(arguments[0]); if (a) a.click();", NEXT_ID
            )
        except WebDriverException:
            pass  # href navigation may tear down the context; turnover is the judge
        first = self._await_turnover(before, timeout=40)
        if first:
            logger.info("[run %s] next-page (anchor href): turned over to first=%s",
                        self.run_id, first)
            return True

        logger.warning("[run %s] next-page: grid did not turn over by either method (still first=%s)",
                       self.run_id, before)
        return False

    # -- orchestration ------------------------------------------------------

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        self._save_run_row()
        try:
            self.start_driver()
            self.open_current_solicitations()
            self.search()
            self.scrape_all_pages()

            self.set_step("generating_excel")
            self.excel_path = self.run_dir / f"Wisconsin_{datetime.now():%Y-%m-%d_%H-%M-%S}.xlsx"
            try:
                export.generate_excel(self.run_id, self.excel_path)
                run_manager.update_run(self.run_id, excel_path=str(self.excel_path), excel_exported=True)
            except Exception:  # noqa: BLE001 — DB unavailable: fall back to in-memory records
                logger.exception("[run %s] DB Excel generation failed; writing from records", self.run_id)
                try:
                    export.generate_excel_from_records(self._records, self.excel_path)
                    run_manager.update_run(self.run_id, excel_path=str(self.excel_path), excel_exported=True)
                except Exception:  # noqa: BLE001 — never fail the run over the Excel
                    logger.exception("[run %s] Excel generation failed", self.run_id)
                    run_manager.add_error(self.run_id, "excel generation failed (see logs)")

            run_manager.update_run(self.run_id, status="completed", step="done")
        except Exception as exc:  # noqa: BLE001 — a failed run must be reported, not crash the worker
            logger.exception("[run %s] failed", self.run_id)
            self.screenshot("fatal")
            run_manager.add_error(self.run_id, str(exc)[:500])
            run_manager.update_run(self.run_id, status="failed", step="failed")
        finally:
            self.cleanup()
            run_manager.update_run(self.run_id, finished_at=datetime.now().isoformat())
            self._save_run_row()

    def _save_run_row(self) -> None:
        run = run_manager.get_run(self.run_id)
        if not run:
            return
        try:
            export.save_run(run)
        except Exception:  # noqa: BLE001
            logger.exception("[run %s] save_run failed", self.run_id)


def execute_run(run_id: str, keyword: str = "", agency: str = "", nigp_code: str = "") -> None:
    WisconsinScraper(run_id, keyword, agency, nigp_code).run()
