"""Selenium automation for the RideMetro (Bonfire) vendor portal.

Flow: login -> Open Public Opportunities list -> for each opportunity click
"View Opportunity" -> scrape the Project Details section -> download the
"Download All files" zip -> store details in the DB -> generate an Excel from
the DB into the run folder.

NOTE: The selectors below are best-guess placeholders and MUST be verified
against the live portal (run with HEADLESS=false and adjust).
"""

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
from app.core.base_scraper import DOWNLOAD_TIMEOUT, BaseScraper
from app.core.filenames import sanitize_filename, timestamp
from app.scrapers.ridemetro import export

logger = logging.getLogger(__name__)

ZIP_DOWNLOAD_TIMEOUT = 300  # zips can be large

# column key -> visible label in the Project Details section
PROJECT_DETAIL_FIELDS: dict[str, str] = {
    "project": "Project",
    "ref_number": "Ref. #",
    "department": "Department",
    "opportunity_type": "Type",
    "status": "Status",
    "open_date": "Open Date",
    "intent_to_bid_due_date": "Intent to Bid Due Date",
    "question_due_date": "Question Due Date",
    "close_date": "Close Date",
    "days_left": "Days Left",
    "contact_information": "Contact Information",
    "project_description": "Project Description",
}

# ---------------------------------------------------------------------------
# Selectors — placeholders to verify against the live portal (HEADLESS=false)
# ---------------------------------------------------------------------------
SEL = {
    "login_email": (By.CSS_SELECTOR, "input[type='email'], input[name='email'], #email"),
    "login_password": (By.CSS_SELECTOR, "input[type='password'], #password"),
    "login_submit": (By.CSS_SELECTOR, "button[type='submit']"),
    "opportunity_rows": (By.CSS_SELECTOR, "table tbody tr"),
    "view_button": (By.XPATH, ".//a[contains(., 'View')] | .//button[contains(., 'View')]"),
    "next_page": (By.XPATH, "//a[@rel='next'] | //button[@aria-label='Next' and not(@disabled)]"),
    "project_details_section": (By.XPATH, "//*[contains(normalize-space(), 'Project Details')]"),
    "supporting_docs_section": (By.XPATH, "//*[contains(normalize-space(), 'Supporting Documentation')]"),
    "download_all_button": (By.XPATH, "//a[contains(., 'Download All')] | //button[contains(., 'Download All')]"),
}


def _xpath_literal(text: str) -> str:
    """Quote a string for use inside an XPath expression."""
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    parts = text.split("'")
    return "concat('" + "', \"'\", '".join(parts) + "')"


class RideMetroScraper(BaseScraper):
    def __init__(self, run_id: str):
        super().__init__(run_id)
        self.excel_path: Path | None = None

    # -- flow steps ---------------------------------------------------------

    def login(self) -> None:
        self.set_step("logging_in")
        self.driver.get(settings.ridemetro_login_url)
        email = self.wait().until(EC.element_to_be_clickable(SEL["login_email"]))
        email.clear()
        email.send_keys(settings.ridemetro_email)
        password = self.driver.find_element(*SEL["login_password"])
        password.clear()
        password.send_keys(settings.ridemetro_password)
        self.driver.find_element(*SEL["login_submit"]).click()
        self.wait().until(lambda d: "login" not in d.current_url.lower())

    def open_opportunities(self) -> None:
        self.set_step("opening_opportunities")
        self.driver.get(settings.ridemetro_opportunities_url)
        self.wait().until(EC.presence_of_element_located(SEL["opportunity_rows"]))

    def process_all_pages(self) -> None:
        """Walk every page of the opportunities table, processing each row."""
        processed = 0
        while True:
            count = len(self.driver.find_elements(*SEL["opportunity_rows"]))
            for index in range(count):
                self.process_opportunity(index)
                processed += 1
                run_manager.update_run(self.run_id, bids_found=max(processed, count))

            next_buttons = self.driver.find_elements(*SEL["next_page"])
            if not next_buttons:
                break
            next_buttons[0].click()
            time.sleep(1)
            self.wait().until(EC.presence_of_element_located(SEL["opportunity_rows"]))
        run_manager.update_run(self.run_id, bids_found=processed)

    def process_opportunity(self, index: int) -> None:
        """Open one opportunity (by row index), scrape it, download its zip, store it.

        Always restores the list context and records a result — a failure on one
        opportunity never aborts the run.
        """
        result: dict[str, Any] = {"ref_number": None, "project": None, "documents": [], "error": None}
        before = self.driver.window_handles
        new_tab = False
        try:
            rows = self.driver.find_elements(*SEL["opportunity_rows"])
            if index >= len(rows):
                return
            button = rows[index].find_element(*SEL["view_button"])
            self.scroll_into_view(button)
            self.set_step(f"opening_opportunity:{index + 1}")
            button.click()
            time.sleep(1)

            after = self.driver.window_handles
            new_tab = len(after) > len(before)
            if new_tab:
                self.driver.switch_to.window(after[-1])
            self.wait().until(EC.presence_of_element_located(SEL["project_details_section"]))

            details = self.scrape_project_details()
            url = self.driver.current_url
            result["ref_number"] = details.get("ref_number")
            result["project"] = details.get("project")

            zip_name = None
            try:
                zip_name = self.download_all_files(details)
                if zip_name:
                    result["documents"].append(zip_name)
            except (TimeoutException, WebDriverException) as exc:
                result["error"] = f"download failed: {exc.__class__.__name__}"
                self.screenshot(f"download_{index}")

            try:
                export.save_bid(self.run_id, details, url, zip_name)
            except Exception:  # noqa: BLE001 — DB issues shouldn't abort the run
                logger.exception("[run %s] save_bid failed for row %s", self.run_id, index)
                run_manager.add_error(self.run_id, f"db save failed for opportunity {index + 1}")
        except (TimeoutException, WebDriverException) as exc:
            result["error"] = str(exc)[:300]
            run_manager.add_error(self.run_id, f"opportunity {index + 1}: {exc.__class__.__name__}")
            self.screenshot(f"opportunity_{index}")
        finally:
            # Return to the opportunities list.
            try:
                if new_tab:
                    self.driver.close()
                    self.driver.switch_to.window(before[0])
                else:
                    self.driver.back()
                    self.wait().until(EC.presence_of_element_located(SEL["opportunity_rows"]))
            except WebDriverException:
                logger.exception("[run %s] failed to return to list after row %s", self.run_id, index)
            run_manager.add_bid_result(self.run_id, result)

    def scrape_project_details(self) -> dict[str, Any]:
        self.set_step("scraping_project_details")
        details: dict[str, Any] = {"raw_data": {}}
        for key, label in PROJECT_DETAIL_FIELDS.items():
            value = self._read_field(label)
            if value:
                details[key] = value
                details["raw_data"][label] = value
        return details

    def _read_field(self, label: str) -> str | None:
        """Best-effort: find the label element and read its adjacent value."""
        lit = _xpath_literal(label)
        candidates = [
            f"//*[normalize-space(text())={lit}]/following-sibling::*[1]",
            f"//*[normalize-space(text())={lit}]/../following-sibling::*[1]",
            f"//dt[normalize-space(text())={lit}]/following-sibling::dd[1]",
        ]
        for xpath in candidates:
            try:
                element = self.driver.find_element(By.XPATH, xpath)
                text = element.text.strip()
                if text:
                    return text
            except WebDriverException:
                continue
        return None

    def download_all_files(self, details: dict[str, Any]) -> str | None:
        ref = details.get("ref_number") or "opportunity"
        self.set_step(f"downloading_zip:{ref}")
        section = self.wait().until(EC.presence_of_element_located(SEL["supporting_docs_section"]))
        self.scroll_into_view(section)
        button = self.wait().until(EC.element_to_be_clickable(SEL["download_all_button"]))
        self.scroll_into_view(button)
        button.click()

        downloaded = self.wait_for_download(timeout=ZIP_DOWNLOAD_TIMEOUT)
        project = details.get("project") or ""
        base = sanitize_filename(f"{ref} - {project}".strip(" -")) or ref
        target = self._unique_path(self.run_dir / f"{base}{downloaded.suffix or '.zip'}")
        shutil.move(str(downloaded), str(target))
        return target.name

    @staticmethod
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

    # -- orchestration ------------------------------------------------------

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        self._save_run_row()  # initial run row (best-effort)
        try:
            self.start_driver()
            self.login()
            self.open_opportunities()
            self.process_all_pages()

            try:
                label = (run_manager.get_run(self.run_id) or {}).get("label") or timestamp()
                self.excel_path = self.run_dir / f"RideMetro_Bids ({label}).xlsx"
                self.set_step("generating_excel")
                export.generate_excel(self.run_id, self.excel_path)
                run_manager.update_run(self.run_id, excel_path=str(self.excel_path), excel_exported=True)
            except Exception:  # noqa: BLE001 — Excel comes from the DB; a DB issue shouldn't fail the run
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
            self._save_run_row()  # final counts (best-effort)

    def _save_run_row(self) -> None:
        run = run_manager.get_run(self.run_id)
        if not run:
            return
        try:
            export.save_run(run)
        except Exception:  # noqa: BLE001
            logger.exception("[run %s] save_run failed", self.run_id)


def execute_run(run_id: str) -> None:
    RideMetroScraper(run_id).run()
