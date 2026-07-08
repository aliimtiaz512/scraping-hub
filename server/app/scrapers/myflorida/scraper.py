"""Selenium automation for the MFMP vendor portal.

Flow: login -> Advertisements -> Advanced Search -> commodity codes ->
Search -> open each bid -> download documents -> Export Excel -> store in DB.

NOTE: The selectors below are best-guess placeholders and MUST be verified
against the live portal (run with HEADLESS=false and adjust).
"""

import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC

from app.config import settings
from app.core import run_manager
from app.core.base_scraper import BaseScraper
from app.core.filenames import sanitize_filename
from app.scrapers.myflorida.ingest import ingest_excel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Selectors — placeholders to verify against the live portal (HEADLESS=false)
# ---------------------------------------------------------------------------
SEL = {
    "login_email": (By.CSS_SELECTOR, "input[type='email'], input[name='username'], #username"),
    "login_password": (By.CSS_SELECTOR, "input[type='password'], #password"),
    "login_submit": (By.CSS_SELECTOR, "button[type='submit']"),
    "nav_advertisements": (By.PARTIAL_LINK_TEXT, "Advertisement"),
    "advanced_search_link": (By.PARTIAL_LINK_TEXT, "Advanced Search"),
    "commodity_dropdown": (By.XPATH, "//*[contains(text(), 'Commodity Code')]"),
    "commodity_input": (By.CSS_SELECTOR, "input[placeholder*='ommodity'], input[aria-label*='ommodity']"),
    "search_button": (By.XPATH, "//button[contains(., 'Search')]"),
    "results_table_rows": (By.CSS_SELECTOR, "table tbody tr"),
    "bid_number_link": (By.CSS_SELECTOR, "td:first-child a"),
    "bid_title_cell": (By.CSS_SELECTOR, "td:nth-child(2)"),
    "document_links": (By.XPATH, "//a[contains(@href, 'download') or contains(@class, 'document')]"),
    "next_page": (By.XPATH, "//button[@aria-label='Next page' and not(@disabled)]"),
    "export_excel": (By.XPATH, "//button[contains(., 'Export')]"),
}


class MFMPScraper(BaseScraper):
    def __init__(self, run_id: str, codes: list[str]):
        super().__init__(run_id)
        self.codes = codes
        self.excel_path: Path | None = None

    # -- flow steps ---------------------------------------------------------

    def login(self) -> None:
        self.set_step("logging_in")
        self.driver.get(settings.mfmp_login_url)
        email = self.wait().until(EC.element_to_be_clickable(SEL["login_email"]))
        email.clear()
        email.send_keys(settings.mfmp_email)
        password = self.driver.find_element(*SEL["login_password"])
        password.clear()
        password.send_keys(settings.mfmp_password)
        self.driver.find_element(*SEL["login_submit"]).click()
        # Successful login leaves the /login page.
        self.wait().until(lambda d: "login" not in d.current_url.lower())

    def open_advertisements(self) -> None:
        self.set_step("opening_advertisements")
        link = self.wait().until(EC.element_to_be_clickable(SEL["nav_advertisements"]))
        link.click()

    def open_advanced_search(self) -> None:
        self.set_step("opening_advanced_search")
        link = self.wait().until(EC.presence_of_element_located(SEL["advanced_search_link"]))
        self.scroll_into_view(link)
        self.wait().until(EC.element_to_be_clickable(SEL["advanced_search_link"])).click()

    def enter_commodity_codes(self) -> None:
        self.set_step("entering_commodity_codes")
        dropdown = self.wait().until(EC.element_to_be_clickable(SEL["commodity_dropdown"]))
        self.scroll_into_view(dropdown)
        dropdown.click()
        code_input = self.wait().until(EC.element_to_be_clickable(SEL["commodity_input"]))
        for code in self.codes:
            code_input.send_keys(code)
            code_input.send_keys(Keys.ENTER)
            time.sleep(0.5)  # let the widget register the selection

    def submit_search(self) -> None:
        self.set_step("searching")
        button = self.wait().until(EC.element_to_be_clickable(SEL["search_button"]))
        self.scroll_into_view(button)
        button.click()
        self.wait().until(EC.presence_of_element_located(SEL["results_table_rows"]))

    def collect_bids(self) -> list[dict]:
        """Read (number, title) for every result row across all pages."""
        self.set_step("collecting_bids")
        bids: list[dict] = []
        while True:
            rows = self.driver.find_elements(*SEL["results_table_rows"])
            for row in rows:
                try:
                    number = row.find_element(*SEL["bid_number_link"]).text.strip()
                    title = row.find_element(*SEL["bid_title_cell"]).text.strip()
                    if number:
                        bids.append({"number": number, "title": title})
                except WebDriverException:
                    continue
            next_buttons = self.driver.find_elements(*SEL["next_page"])
            if not next_buttons:
                break
            next_buttons[0].click()
            time.sleep(1)
        run_manager.update_run(self.run_id, bids_found=len(bids))
        return bids

    def process_bid(self, bid: dict) -> dict:
        """Open a bid's detail page and download all of its documents."""
        number, title = bid["number"], bid["title"]
        self.set_step(f"downloading_documents:{number}")
        result = {"number": number, "title": title, "documents": [], "error": None}

        link = self.wait().until(EC.element_to_be_clickable((By.LINK_TEXT, number)))
        link.click()

        bid_dir = self.run_dir / sanitize_filename(title or number)
        bid_dir.mkdir(parents=True, exist_ok=True)

        doc_links = self.driver.find_elements(*SEL["document_links"])
        for index, doc_link in enumerate(doc_links, start=1):
            try:
                self.scroll_into_view(doc_link)
                doc_link.click()
                downloaded = self.wait_for_download()
                new_name = f"{sanitize_filename(title or number)}_{index}{downloaded.suffix}"
                target = bid_dir / new_name
                shutil.move(str(downloaded), str(target))
                result["documents"].append(new_name)
            except (TimeoutException, WebDriverException, OSError) as exc:
                result.setdefault("document_errors", []).append(f"doc {index}: {exc.__class__.__name__}")

        self.driver.back()
        self.wait().until(EC.presence_of_element_located(SEL["results_table_rows"]))
        return result

    def export_excel(self) -> Path:
        self.set_step("exporting_excel")
        button = self.wait().until(EC.element_to_be_clickable(SEL["export_excel"]))
        button.click()
        downloaded = self.wait_for_download()
        target = self.run_dir / f"bids_export{downloaded.suffix or '.xlsx'}"
        shutil.move(str(downloaded), str(target))
        self.excel_path = target
        run_manager.update_run(self.run_id, excel_exported=True)
        return target

    def ingest_to_db(self) -> None:
        """Load the exported Excel into Postgres. Best-effort — a DB failure
        does not fail the scrape run (the files on disk are the source of truth)."""
        if not self.excel_path or not self.excel_path.exists():
            return
        self.set_step("storing_in_db")
        run = run_manager.get_run(self.run_id) or {"run_id": self.run_id}
        stored = ingest_excel(self.excel_path, run)
        run_manager.update_run(self.run_id, bids_stored_in_db=stored)

    # -- orchestration ------------------------------------------------------

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        try:
            self.start_driver()
            self.login()
            self.open_advertisements()
            self.open_advanced_search()
            self.enter_commodity_codes()
            self.submit_search()
            bids = self.collect_bids()

            for bid in bids:
                try:
                    result = self.process_bid(bid)
                except (TimeoutException, WebDriverException) as exc:
                    result = {**bid, "documents": [], "error": str(exc)[:300]}
                    run_manager.add_error(self.run_id, f"bid {bid['number']}: {exc.__class__.__name__}")
                    self.screenshot(f"bid_{bid['number']}")
                run_manager.add_bid_result(self.run_id, result)

            exported = False
            try:
                self.export_excel()
                exported = True
            except (TimeoutException, WebDriverException) as exc:
                run_manager.add_error(self.run_id, f"excel export failed: {exc.__class__.__name__}")
                self.screenshot("export_excel")

            if exported:
                try:
                    self.ingest_to_db()
                except Exception as exc:  # noqa: BLE001 — DB issues shouldn't fail the run
                    logger.exception("[run %s] DB ingestion failed", self.run_id)
                    run_manager.add_error(self.run_id, f"db ingestion failed: {exc.__class__.__name__}")

            run_manager.update_run(self.run_id, status="completed", step="done")
        except Exception as exc:  # noqa: BLE001 — a failed run must be reported, not crash the worker
            logger.exception("[run %s] failed", self.run_id)
            self.screenshot("fatal")
            run_manager.add_error(self.run_id, str(exc)[:500])
            run_manager.update_run(self.run_id, status="failed", step="failed")
        finally:
            self.cleanup()
            run_manager.update_run(self.run_id, finished_at=datetime.now().isoformat())


def execute_run(run_id: str, codes: list[str]) -> None:
    MFMPScraper(run_id, codes).run()
