"""Selenium automation for the North Dakota (ND Buys) public procurement portal.

ND Buys is an Ivalua platform (all controls carry data-iv-* attributes). Flow:
open the supplier login page -> click "ND Supplier Login" (an OAuth button that
redirects to ND's Azure AD B2C sign-in) -> enter the User ID / Password -> land
back on ND Buys -> open the "Solicitations" menu and choose "Public Solicitation
Request" -> enter a keyword (and, best-effort, a commodity) and Search -> page
through the whole results grid, storing every row (RFx Name, publication/bid
dates, commodities, remaining time, status) in the DB -> generate an Excel from
the DB into the run folder.

The B2C sign-in page carries an (invisible) reCAPTCHA; a headless session can be
challenged and blocked. When the login does not complete we fail with a clear
message rather than a bare timeout.
"""

import logging
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
from app.scrapers.northdakota import export

logger = logging.getLogger(__name__)

# -- login (ND Buys + Azure AD B2C) ------------------------------------------
OAUTH_BTN_ID = "body_x_btnOAuth"        # "ND Supplier Login" on the ND Buys login page
B2C_USER_ID = "signInName"              # User ID field on the B2C sign-in page
B2C_PASSWORD_ID = "password"            # Password field on the B2C sign-in page

# -- search page (Public Solicitation Request) -------------------------------
KEYWORD_ID = "body_x_txtQuery"
COMMODITY_SEARCH_ID = "body_x_selFamily_search"   # Ivalua autocomplete text input
SEARCH_BTN_ID = "body_x_prxFilterBar_x_cmdSearchBtn"

# -- results grid ------------------------------------------------------------
GRID_ID = "body_x_grid_grd"
ROW_CSS = "#body_x_grid_grd tbody tr[data-id]"
NEXT_BTN_ID = "body_x_grid_gridPagerBtnNextPage"

PREVIEW_LIMIT = 100   # rows mirrored to the live run state for the UI table
MAX_PAGES = 200       # pagination safety guard


class NorthDakotaScraper(BaseScraper):
    def __init__(self, run_id: str, keyword: str = "", commodity: str = ""):
        super().__init__(run_id)
        self.keyword = keyword.strip()
        self.commodity = commodity.strip()
        self.excel_path: Path | None = None
        # Full in-memory copy of every scraped row — the Excel fallback source if
        # the DB is unavailable.
        self._records: list[dict[str, Any]] = []

    # -- helpers ------------------------------------------------------------

    def _abs_url(self, href: str | None) -> str | None:
        if not href:
            return None
        if href.startswith("http"):
            return href
        base = settings.northdakota_base_url.rstrip("/")
        return base + ("" if href.startswith("/") else "/") + href

    def _click_by_text(self, tags: list[str], text: str, timeout: int = 20) -> None:
        """Click the first clickable element among `tags` whose text matches `text`.

        Ivalua menus render items as buttons/anchors/list items; matching on the
        visible label keeps us off brittle generated ids.
        """
        conditions = " or ".join(
            f"self::{tag}" for tag in tags
        )
        xpath = (
            f"//*[({conditions})]"
            f"[contains(normalize-space(.), {_xpath_literal(text)})]"
        )
        el = self.wait(timeout).until(EC.element_to_be_clickable((By.XPATH, xpath)))
        self.scroll_into_view(el)
        el.click()

    # -- flow steps ---------------------------------------------------------

    def login(self) -> None:
        self.set_step("logging_in")
        self.driver.get(settings.northdakota_login_url)
        # "ND Supplier Login" -> redirects to the ND Azure AD B2C sign-in page.
        self.wait(30).until(EC.element_to_be_clickable((By.ID, OAUTH_BTN_ID))).click()

        # B2C sign-in: User ID + Password. The page carries an invisible reCAPTCHA
        # that a headless session can trip; if the fields never appear, say so.
        try:
            self.wait(40).until(EC.presence_of_element_located((By.ID, B2C_USER_ID)))
        except TimeoutException as exc:
            self.screenshot("b2c_login_missing")
            raise WebDriverException(
                "ND Buys sign-in page did not load its User ID field — the OAuth "
                "redirect or an invisible reCAPTCHA may have blocked the headless "
                "browser."
            ) from exc

        self.driver.find_element(By.ID, B2C_USER_ID).send_keys(settings.northdakota_username)
        self.driver.find_element(By.ID, B2C_PASSWORD_ID).send_keys(settings.northdakota_password)
        self._submit_b2c()

        # Back on ND Buys once the top-nav "Solicitations" menu is present. If the
        # password field is still on screen, the credentials/reCAPTCHA were rejected.
        if not self._await_logged_in(timeout=60):
            self.screenshot("login_not_completed")
            raise WebDriverException(
                "ND Buys login did not complete — check the North Dakota credentials "
                "in server/.env, or the B2C reCAPTCHA blocked the headless session."
            )

    def _submit_b2c(self) -> None:
        """Submit the B2C sign-in form. The default policy button is #next; fall
        back to any submit button inside the sign-in form."""
        for locator in (
            (By.ID, "next"),
            (By.ID, "continue"),
            (By.CSS_SELECTOR, "#attributeList button[type='submit']"),
            (By.CSS_SELECTOR, "button[type='submit']"),
        ):
            try:
                btn = self.driver.find_element(*locator)
            except WebDriverException:
                continue
            try:
                self.scroll_into_view(btn)
                btn.click()
                return
            except WebDriverException:
                continue
        # Last resort: submit the password field's form directly.
        try:
            self.driver.find_element(By.ID, B2C_PASSWORD_ID).submit()
        except WebDriverException:
            pass

    def _await_logged_in(self, timeout: int) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.driver.find_elements(
                    By.XPATH, "//button[contains(@class,'menu-button')][contains(normalize-space(.),'Solicitations')]"
                ):
                    return True
            except WebDriverException:
                pass
            time.sleep(1)
        return False

    def open_public_solicitations(self) -> None:
        self.set_step("opening_solicitations")
        # Open the "Solicitations" top-nav menu, then choose "Public Solicitation
        # Request". The dropdown item's exact markup varies, so match by label.
        self._click_by_text(["button"], "Solicitations", timeout=30)
        time.sleep(1)
        self.set_step("opening_public_solicitation_request")
        self._click_by_text(["a", "button", "span", "div", "li"], "Public Solicitation Request", timeout=20)
        self.wait(40).until(EC.presence_of_element_located((By.ID, KEYWORD_ID)))

    def search(self) -> None:
        self.set_step("searching")
        if self.keyword:
            field = self.wait().until(EC.presence_of_element_located((By.ID, KEYWORD_ID)))
            field.clear()
            field.send_keys(self.keyword)
        if self.commodity:
            self._select_commodity(self.commodity)

        first_before = self._first_row_id()
        self.wait().until(EC.element_to_be_clickable((By.ID, SEARCH_BTN_ID))).click()
        # Ivalua re-renders the grid via AJAX; wait for it to settle on the new set.
        self._wait_grid_settled(first_before)

    def _select_commodity(self, commodity: str) -> None:
        """Best-effort: type into the Ivalua commodities autocomplete and pick the
        first suggestion. A failure here is non-fatal — the keyword still applies."""
        try:
            box = self.driver.find_element(By.ID, COMMODITY_SEARCH_ID)
            self.scroll_into_view(box)
            box.click()
            box.send_keys(commodity)
            # Wait for the dropdown menu to populate, then click the first result.
            item = self.wait(10).until(EC.element_to_be_clickable((
                By.CSS_SELECTOR,
                ".ui.dropdown.dropdown-selector .menu .item, .ui.search.keywords .results .result",
            )))
            item.click()
        except WebDriverException:
            logger.info("[run %s] commodity autocomplete failed for %r; continuing keyword-only",
                        self.run_id, commodity)
            run_manager.add_warning(self.run_id, f"could not apply commodity filter '{commodity}'")

    # -- results ------------------------------------------------------------

    _JS_SCRAPE = """
        const norm = (el) => ((el ? el.textContent : '') || '').replace(/\\s+/g, ' ').trim();
        const table = document.getElementById('body_x_grid_grd');
        if (!table) return [];
        const out = [];
        for (const tr of table.querySelectorAll('tbody > tr[data-id]')) {
          const tds = tr.querySelectorAll('td');
          if (tds.length < 9) continue;
          const link = tds[0].querySelector('a');
          out.push({
            rfp_id: tr.getAttribute('data-id'),
            detail_url: link ? link.getAttribute('href') : null,
            title: norm(tds[1]),
            pub_begin_date: norm(tds[2]),
            pub_end_date: norm(tds[3]),
            begin_date: norm(tds[4]),
            close_date: norm(tds[5]),
            commodity: norm(tds[6]),
            remaining_time: norm(tds[7]),
            status: norm(tds[8]),
          });
        }
        return out;
    """

    def _read_rows(self) -> list[dict[str, Any]]:
        try:
            data = self.driver.execute_script(self._JS_SCRAPE)
        except WebDriverException:
            return []
        rows: list[dict[str, Any]] = []
        for row in data or []:
            if not row.get("rfp_id"):
                continue
            row["detail_url"] = self._abs_url(row.get("detail_url"))
            row["matched_keyword"] = self.keyword or self.commodity or ""
            rows.append(row)
        return rows

    def _first_row_id(self) -> str | None:
        try:
            return self.driver.execute_script(
                "var tr = document.querySelector(\"#body_x_grid_grd tbody tr[data-id]\");"
                "return tr ? tr.getAttribute('data-id') : null;"
            )
        except WebDriverException:
            return None

    def _row_count(self) -> int:
        try:
            return int(self.driver.execute_script(
                "return document.querySelectorAll(\"#body_x_grid_grd tbody tr[data-id]\").length;"
            ) or 0)
        except WebDriverException:
            return 0

    def _wait_grid_settled(self, first_before: str | None, timeout: int = 40) -> int:
        """Wait for the grid to finish repainting after a search/page change.

        The first row's data-id changing signals a new page loaded; when the set
        is unchanged (e.g. a keyword that returns the same first row) we fall back
        to waiting for the row count to hold steady across reads.
        """
        deadline = time.monotonic() + timeout
        prev = -1
        stable = 0
        while time.monotonic() < deadline:
            try:
                self.wait(5).until(EC.presence_of_element_located((By.CSS_SELECTOR, ROW_CSS)))
            except TimeoutException:
                # No rows at all — could be a legitimate zero-result search.
                if time.monotonic() - (deadline - timeout) > 6:
                    return 0
                time.sleep(0.4)
                continue
            count = self._row_count()
            first = self._first_row_id()
            if first_before is not None and first and first != first_before:
                return count
            if count > 0 and count == prev:
                stable += 1
                if stable >= 2:
                    return count
            else:
                stable = 0
            prev = count
            time.sleep(0.5)
        return self._row_count()

    def _next_disabled(self) -> bool:
        try:
            return bool(self.driver.execute_script(
                "var b = document.getElementById(arguments[0]);"
                "return b ? (b.className.indexOf('disabled') !== -1 || b.disabled) : true;",
                NEXT_BTN_ID,
            ))
        except WebDriverException:
            return True

    def _go_next_page(self) -> bool:
        """Advance one page via the grid's Next button, confirmed by the first
        row's data-id turning over."""
        if self._next_disabled():
            return False
        before = self._first_row_id()
        try:
            btn = self.wait(10).until(EC.element_to_be_clickable((By.ID, NEXT_BTN_ID)))
            self.scroll_into_view(btn)
            btn.click()
        except WebDriverException:
            # Fall back to invoking the button's own onclick (its javascript: postback).
            try:
                self.driver.execute_script(
                    "var b = document.getElementById(arguments[0]); if (b) b.click();", NEXT_BTN_ID
                )
            except WebDriverException:
                return False
        return self._wait_turnover(before, timeout=40)

    def _wait_turnover(self, before: str | None, timeout: int) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.6)
            first = self._first_row_id()
            if first and first != before:
                return True
        logger.info("[run %s] next-page: grid did not turn over (still first=%s)", self.run_id, before)
        return False

    def scrape_all_pages(self) -> None:
        self.set_step("scraping_results")
        preview: list[dict[str, Any]] = []
        seen: set[str] = set()
        scraped = 0
        pages = 0
        while True:
            pages += 1
            for rec in self._read_rows():
                key = rec.get("rfp_id")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                self._records.append(rec)
                scraped += 1
                if len(preview) < PREVIEW_LIMIT:
                    preview.append({**rec, "documents": [], "error": None})
            run_manager.update_run(
                self.run_id, bids_found=scraped, bids_processed=scraped, bids=list(preview)
            )
            logger.info("[run %s] page %s scraped (total %s)", self.run_id, pages, scraped)

            if pages >= MAX_PAGES:
                run_manager.add_error(self.run_id, f"stopped at page cap ({MAX_PAGES})")
                break
            if not self._go_next_page():
                break

    # -- orchestration ------------------------------------------------------

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        self._save_run_row()
        try:
            self.start_driver()
            self.login()
            self.open_public_solicitations()
            self.search()
            self.scrape_all_pages()

            if not self._records:
                run_manager.update_run(self.run_id, no_results=True)

            # Persist every scraped solicitation in one transaction (mirrors
            # MyFlorida). Best-effort: a DB failure must not fail the run — the
            # Excel is then written straight from the in-memory records.
            run = run_manager.get_run(self.run_id) or {"run_id": self.run_id}
            db_ok = True
            try:
                stored = export.save_bids(run, self._records)
                run_manager.update_run(self.run_id, bids_stored_in_db=stored)
            except Exception:  # noqa: BLE001 — DB issues shouldn't abort the run
                db_ok = False
                logger.exception("[run %s] DB save failed", self.run_id)
                run_manager.add_error(self.run_id, "db save failed (see logs)")

            self.set_step("generating_excel")
            self.excel_path = self.run_dir / f"NorthDakota_{datetime.now():%Y-%m-%d_%H-%M-%S}.xlsx"
            try:
                if db_ok:
                    export.generate_excel(self.run_id, self.excel_path)
                else:
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


def _xpath_literal(text: str) -> str:
    """Quote a string for use inside an XPath expression."""
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    parts = text.split("'")
    return "concat('" + "', \"'\", '".join(parts) + "')"


def execute_run(run_id: str, keyword: str = "", commodity: str = "") -> None:
    NorthDakotaScraper(run_id, keyword, commodity).run()
