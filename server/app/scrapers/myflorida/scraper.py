"""Selenium automation for the MFMP vendor portal.

Flow: login -> Advertisements -> Advanced Search -> commodity codes ->
Search -> open each bid -> download documents -> Export Excel -> store in DB.

The portal is an Angular Material single-page app. Selectors below were verified
against the live site; the fiddly parts are the commodity-code control (a
ngx-mat-select-search multi-select whose options load asynchronously and which
stays disabled until they do) and the CDK overlay backdrops that intercept
clicks until they finish animating out.
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
# Selectors — verified against the live Angular Material portal.
# ---------------------------------------------------------------------------
ADS_URL = "https://vendor.myfloridamarketplace.com/vendor/ads"

SEL = {
    "login_email": (By.CSS_SELECTOR, "input[formcontrolname='username']"),
    "login_password": (By.CSS_SELECTOR, "input[formcontrolname='password']"),
    "login_submit": (By.CSS_SELECTOR, "button[type='submit']"),
    "advanced_search_button": (By.XPATH, "//button[contains(., 'Advanced Search')]"),
    "max_results_select": (By.XPATH, "//mat-form-field[.//mat-label[contains(.,'Maximum')]]//mat-select"),
    "ad_status_panel_header": (By.XPATH, "//mat-expansion-panel-header[.//mat-panel-title[contains(normalize-space(.),'Ad Status')]]"),
    "ad_status_options": (By.XPATH, "//mat-selection-list[@aria-label='Ad Status']//mat-list-option"),
    "commodity_panel_header": (By.XPATH, "//mat-expansion-panel-header[.//*[contains(text(),'Commodity')]]"),
    "commodity_select": (By.ID, "mat-select-commodity-code"),
    "overlay_search_input": (By.CSS_SELECTOR, ".cdk-overlay-container input.mat-select-search-input:not(.mat-select-search-hidden)"),
    "overlay_options": (By.CSS_SELECTOR, ".cdk-overlay-container mat-option"),
    "overlay_backdrop": (By.CSS_SELECTOR, ".cdk-overlay-backdrop-showing"),
    "search_button": (By.XPATH, "//button[normalize-space(.)='Search']"),
    "results_rows": (By.CSS_SELECTOR, "tbody tr"),
    "document_links": (By.CSS_SELECTOR, "a.document-link"),
    "export_excel": (By.XPATH, "//button[contains(., 'Export')]"),
}

# The advanced-search results table columns, in order: Title, Number, Agency Ad
# Number, Version, Organization, Ad Type, Start Date, End Date. Title is cell 1;
# the clickable Number link is cell 2.
MAX_RESULTS = "100"  # portal offers 25/50/75/100

# Ad Status options, keyed by API value -> the list-option text shown in the portal.
AD_STATUS_LABELS = {
    "preview": "PREVIEW",
    "open": "OPEN",
    "closed": "CLOSED",
    "withdrawn": "WITHDRAWN",
}


class MFMPScraper(BaseScraper):
    def __init__(self, run_id: str, codes: list[str], ad_statuses: list[str] | None = None):
        super().__init__(run_id)
        self.codes = codes
        # An empty list means: leave the portal's Ad Status list untouched (every status).
        self.ad_statuses = [s for s in (ad_statuses or []) if s in AD_STATUS_LABELS]
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
        self.driver.get(ADS_URL)
        self.wait().until(EC.element_to_be_clickable(SEL["advanced_search_button"]))

    def open_advanced_search(self) -> None:
        self.set_step("opening_advanced_search")
        self.wait().until(EC.element_to_be_clickable(SEL["advanced_search_button"])).click()
        self._set_max_results(MAX_RESULTS)
        # Expand the Commodity Codes accordion so its multi-select renders.
        header = self.wait().until(EC.element_to_be_clickable(SEL["commodity_panel_header"]))
        self.scroll_into_view(header)
        if header.get_attribute("aria-expanded") != "true":
            header.click()
        # The select loads its options asynchronously and stays disabled until ready.
        self.wait().until(self._commodity_enabled)

    def enter_commodity_codes(self) -> None:
        self.set_step("entering_commodity_codes")
        for code in self.codes:
            try:
                self._select_one_code(code)
            except (TimeoutException, WebDriverException):
                run_manager.add_error(self.run_id, f"commodity code {code}: not selectable")
                self._dismiss_overlay()

    def select_ad_status(self) -> None:
        """Select one or more Ad Status filters (Preview/Open/Closed/Withdrawn).

        The control is an expansion panel wrapping a mat-selection-list; options are
        inline mat-list-option items (no CDK overlay) and the list is multi-select, so
        each requested status is toggled on. When none are requested we leave the list
        untouched so the portal returns ads of every status. Best-effort — a failure
        here never fails the run.
        """
        if not self.ad_statuses:
            return
        self.set_step("selecting_ad_status")
        wanted = {AD_STATUS_LABELS[s].lower() for s in self.ad_statuses}
        try:
            header = self.wait().until(EC.element_to_be_clickable(SEL["ad_status_panel_header"]))
            self.scroll_into_view(header)
            if header.get_attribute("aria-expanded") != "true":
                header.click()
            options = self.wait().until(EC.presence_of_all_elements_located(SEL["ad_status_options"]))
            matched = set()
            for option in options:
                # Read textContent rather than .text: the label lives in a nested
                # .mat-list-text div and Selenium's .text returns "" for options that
                # are below the fold or mid-animation, which would spuriously report
                # every status as "option not found".
                text = (option.get_attribute("textContent") or "").strip().lower()
                if text in wanted:
                    self.scroll_into_view(option)
                    option.click()
                    time.sleep(0.5)
                    matched.add(text)
            for missing in wanted - matched:
                run_manager.add_error(self.run_id, f"ad status {missing.upper()}: option not found")
        except (TimeoutException, WebDriverException):
            labels = ", ".join(AD_STATUS_LABELS[s] for s in self.ad_statuses)
            run_manager.add_error(self.run_id, f"ad status ({labels}): not selectable")

    def submit_search(self) -> None:
        self.set_step("searching")
        button = self.wait().until(EC.element_to_be_clickable(SEL["search_button"]))
        self.scroll_into_view(button)
        button.click()
        # Results render asynchronously; tolerate an empty result set.
        try:
            self.wait(20).until(EC.presence_of_element_located(SEL["results_rows"]))
        except TimeoutException:
            pass
        time.sleep(2)

    # -- advanced-search helpers -------------------------------------------

    def _commodity_enabled(self, _driver):
        """WebDriverWait predicate: the commodity select once it is no longer disabled."""
        el = self.driver.find_element(*SEL["commodity_select"])
        return el if "mat-select-disabled" not in el.get_attribute("class") else False

    def _wait_no_backdrop(self) -> None:
        """Wait for any CDK overlay backdrop to finish animating out, so it stops
        intercepting the next click. Best-effort — never fatal."""
        try:
            self.wait(10).until_not(EC.presence_of_element_located(SEL["overlay_backdrop"]))
        except TimeoutException:
            pass

    def _dismiss_overlay(self) -> None:
        try:
            self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            self._wait_no_backdrop()
        except WebDriverException:
            pass

    def _set_max_results(self, value: str) -> None:
        """Bump the result cap from the default 25. Best-effort — keep default on failure."""
        try:
            self.wait().until(EC.element_to_be_clickable(SEL["max_results_select"])).click()
            time.sleep(1)
            for option in self.driver.find_elements(*SEL["overlay_options"]):
                if option.text.strip() == value:
                    option.click()
                    break
            self._wait_no_backdrop()
        except (TimeoutException, WebDriverException):
            self._dismiss_overlay()

    def _select_one_code(self, code: str) -> None:
        """Open the commodity multi-select, filter by the code, and click its option."""
        select = self.wait().until(self._commodity_enabled)
        self.scroll_into_view(select)
        select.click()
        search = self.wait().until(EC.element_to_be_clickable(SEL["overlay_search_input"]))
        search.clear()
        search.send_keys(code)
        time.sleep(2)  # search input debounce + async option filter
        for option in self.driver.find_elements(*SEL["overlay_options"]):
            if option.text.strip().startswith(code):
                option.click()
                time.sleep(0.5)
                break
        self._dismiss_overlay()

    def collect_bids(self) -> list[dict]:
        """Read (number, title) for every result row.

        Results are capped by the Maximum Results control (set to 100), so the
        table is a single page — the portal offers no pagination beyond that cap.
        """
        self.set_step("collecting_bids")
        bids: list[dict] = []
        for row in self.driver.find_elements(*SEL["results_rows"]):
            try:
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) < 2:
                    continue
                links = cells[1].find_elements(By.TAG_NAME, "a")
                number = links[0].text.strip() if links else ""
                title = cells[0].text.strip()
                if number:
                    bids.append({"number": number, "title": title})
            except WebDriverException:
                continue
        run_manager.update_run(self.run_id, bids_found=len(bids))
        return bids

    def process_bid(self, bid: dict) -> dict:
        """Open a bid's detail page and download all of its documents.

        The Number cell links via a JS click handler (no href), so we click it,
        wait for the /detail/ route, download each attachment, then navigate back
        — which restores the previous search results.
        """
        number, title = bid["number"], bid["title"]
        self.set_step(f"downloading_documents:{number}")
        result = {"number": number, "title": title, "documents": [], "error": None}

        link = self.wait().until(EC.element_to_be_clickable((By.LINK_TEXT, number)))
        self.scroll_into_view(link)
        link.click()
        self.wait().until(lambda d: "/detail/" in d.current_url)
        time.sleep(2)  # let the detail page (incl. document list) render

        bid_dir = self.run_dir / sanitize_filename(title or number)
        bid_dir.mkdir(parents=True, exist_ok=True)

        doc_links = self.driver.find_elements(*SEL["document_links"])
        for index, doc_link in enumerate(doc_links, start=1):
            try:
                self.scroll_into_view(doc_link)
                # JS click: a stray CDK backdrop can intercept a native click, and
                # the anchor's download is driven by its click handler regardless.
                self.driver.execute_script("arguments[0].click();", doc_link)
                downloaded = self.wait_for_download()
                new_name = f"{sanitize_filename(title or number)}_{index}{downloaded.suffix}"
                target = bid_dir / new_name
                shutil.move(str(downloaded), str(target))
                result["documents"].append(new_name)
            except (TimeoutException, WebDriverException, OSError) as exc:
                result.setdefault("document_errors", []).append(f"doc {index}: {exc.__class__.__name__}")

        self.driver.back()
        self.wait().until(EC.presence_of_element_located(SEL["results_rows"]))
        time.sleep(1)
        return result

    def _recover_to_results(self) -> None:
        """After a bid errors on its detail page, step back so the next bid's
        Number link is reachable again. No-op if already on the results list."""
        try:
            if "/detail/" in self.driver.current_url:
                self.driver.back()
                self.wait().until(EC.presence_of_element_located(SEL["results_rows"]))
                time.sleep(1)
        except (TimeoutException, WebDriverException):
            pass

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
            self.select_ad_status()
            self.submit_search()
            bids = self.collect_bids()

            # Export + ingest first, while we are still on the results page — this
            # captures the run's data even if the per-bid document crawl fails.
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

            for bid in bids:
                try:
                    result = self.process_bid(bid)
                except (TimeoutException, WebDriverException) as exc:
                    result = {**bid, "documents": [], "error": str(exc)[:300]}
                    run_manager.add_error(self.run_id, f"bid {bid['number']}: {exc.__class__.__name__}")
                    self.screenshot(f"bid_{bid['number']}")
                    self._recover_to_results()
                run_manager.add_bid_result(self.run_id, result)

            run_manager.update_run(self.run_id, status="completed", step="done")
        except Exception as exc:  # noqa: BLE001 — a failed run must be reported, not crash the worker
            logger.exception("[run %s] failed", self.run_id)
            self.screenshot("fatal")
            run_manager.add_error(self.run_id, str(exc)[:500])
            run_manager.update_run(self.run_id, status="failed", step="failed")
        finally:
            self.cleanup()
            run_manager.update_run(self.run_id, finished_at=datetime.now().isoformat())


def execute_run(run_id: str, codes: list[str], ad_statuses: list[str] | None = None) -> None:
    MFMPScraper(run_id, codes, ad_statuses).run()
