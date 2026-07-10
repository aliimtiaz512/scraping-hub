"""Selenium automation for the RideMetro (Bonfire) vendor portal.

Flow: login -> open the "Open Public Opportunities" list -> read every row of
the opportunities table -> store the bid info in the DB -> generate an Excel
from the DB into the run folder.

We deliberately do NOT open individual opportunity pages or download documents:
the /opportunities/* pages sit behind a Cloudflare "verify you are human"
challenge that manual browsing avoids but automation trips, and repeatedly
solving it on a real vendor account is risky. Everything we export is read from
the opportunities list, which loads cleanly. To pull an opportunity's documents,
open its "Opportunity URL" in a browser and download them by hand.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC

from app.config import settings
from app.core import run_manager
from app.core.base_scraper import BaseScraper
from app.core.filenames import timestamp
from app.scrapers.ridemetro import export

logger = logging.getLogger(__name__)

# Opportunities-table column header -> RideMetroBid field. The "Action" column
# (the "View Opportunity" link) is handled separately to capture the URL.
COLUMN_KEYS: dict[str, str] = {
    "Status": "status",
    "Ref. #": "ref_number",
    "Project": "project",
    "Department": "department",
    "Close Date": "close_date",
    "Days Left": "days_left",
}

# ---------------------------------------------------------------------------
# Selectors
#
# The Bonfire/Euna portal renders the opportunities list client-side: the
# "Open Public Opportunities" tab is active on load, but its table
# (#DataTables_Table_0) is filled by an AJAX call that only fires after the
# My Opportunities / Auctions calls resolve. So we click the tab and wait for
# the real data rows before reading them.
# ---------------------------------------------------------------------------
SEL = {
    "login_email": (By.CSS_SELECTOR, "input[name='email'], #input-email, input[type='email']"),
    "login_password": (By.CSS_SELECTOR, "input[name='password'], #input-password, input[type='password']"),
    "login_submit": (By.CSS_SELECTOR, "button[type='submit']"),
    "open_opportunities_tab": (By.CSS_SELECTOR, "#openOpportunitiesTab a"),
    "header_cells": (By.CSS_SELECTOR, "#DataTables_Table_0 thead th"),
    "opportunity_rows": (By.CSS_SELECTOR, "#DataTables_Table_0 tbody tr"),
}


class RideMetroScraper(BaseScraper):
    def __init__(self, run_id: str):
        super().__init__(run_id)
        self.excel_path: Path | None = None

    # -- flow steps ---------------------------------------------------------

    def login(self) -> None:
        self.set_step("logging_in")
        self.driver.get(settings.ridemetro_login_url)
        # Bonfire/Euna uses an identifier-first flow: the first screen shows only
        # the email field and a "Continue" button. The password field is not in
        # the DOM until the email is submitted, so we fill and submit email first,
        # then wait for the password field to appear before filling it.
        email = self.wait().until(EC.element_to_be_clickable(SEL["login_email"]))
        email.clear()
        email.send_keys(settings.ridemetro_email)
        self.driver.find_element(*SEL["login_submit"]).click()

        password = self.wait().until(EC.element_to_be_clickable(SEL["login_password"]))
        password.clear()
        password.send_keys(settings.ridemetro_password)

        # Submit and confirm we actually leave the login page. This React form
        # sometimes swallows the submit click when it lands during a re-render
        # (the button stays put with the form still filled), so we verify the
        # navigation and, if it didn't happen, re-submit — pressing Enter in the
        # password field the second time as a more reliable native submit.
        def left_login(d) -> bool:
            return "login" not in d.current_url.lower()

        for attempt in range(3):
            try:
                if attempt == 0:
                    self.driver.find_element(*SEL["login_submit"]).click()
                else:
                    self.driver.find_element(*SEL["login_password"]).send_keys(Keys.RETURN)
            except WebDriverException:
                # Field/button went stale mid-navigation — that usually means the
                # submit already took, so let the wait below decide.
                pass
            try:
                self.wait(15).until(left_login)
                return
            except TimeoutException:
                if attempt == 2:
                    raise

    def open_opportunities(self) -> None:
        """Navigate to the portal and wait for the Open Opportunities rows.

        The tab is active on load, but its table is populated by an AJAX call
        that only fires once the My Opportunities / Auctions calls resolve, so we
        click the tab to be sure it's selected, then wait (generously) for the
        data rows. If the portal genuinely has no open opportunities DataTables
        still renders a single "no projects" row, so this won't hang.
        """
        self.set_step("opening_opportunities")
        self.driver.get(settings.ridemetro_opportunities_url)
        try:
            self.wait().until(EC.element_to_be_clickable(SEL["open_opportunities_tab"])).click()
        except (TimeoutException, WebDriverException):
            pass
        self.wait(60).until(EC.presence_of_element_located(SEL["opportunity_rows"]))

    def scrape_opportunities(self) -> None:
        """Read every row of the opportunities table and store it.

        The whole list renders into #DataTables_Table_0 at once (no server-side
        pagination), so a single pass over the rows captures everything.
        """
        self.set_step("scraping_opportunities")
        # thead cells are visually collapsed (height:0), so read textContent, not
        # the (empty) rendered text, to map columns to fields by header label.
        headers = [
            (th.get_attribute("textContent") or "").strip()
            for th in self.driver.find_elements(*SEL["header_cells"])
        ]
        rows = self.driver.find_elements(*SEL["opportunity_rows"])
        processed = 0
        for index, row in enumerate(rows):
            details, url = self._extract_row(row, headers)
            # Skip the "There are no open projects at this time." placeholder row.
            if not details.get("ref_number"):
                continue
            result: dict[str, Any] = {
                "ref_number": details.get("ref_number"),
                "project": details.get("project"),
                "documents": [],
                "error": None,
            }
            try:
                export.save_bid(self.run_id, details, url, None)
            except Exception:  # noqa: BLE001 — DB issues shouldn't abort the run
                logger.exception("[run %s] save_bid failed for row %s", self.run_id, index)
                run_manager.add_error(self.run_id, f"db save failed for opportunity {index + 1}")
                result["error"] = "db save failed"
            run_manager.add_bid_result(self.run_id, result)
            processed += 1
            run_manager.update_run(self.run_id, bids_found=processed)
        run_manager.update_run(self.run_id, bids_found=processed)

    def _extract_row(self, row, headers: list[str]) -> tuple[dict[str, Any], str | None]:
        """Pull the bid fields and the opportunity URL out of one table row."""
        cells = row.find_elements(By.TAG_NAME, "td")
        details: dict[str, Any] = {"raw_data": {}}
        url: str | None = None
        for i, header in enumerate(headers):
            if i >= len(cells):
                break
            cell = cells[i]
            key = COLUMN_KEYS.get(header)
            if key:
                value = cell.text.strip()
                if value:
                    details[key] = value
                    details["raw_data"][header] = value
            elif header == "Action":
                links = cell.find_elements(By.CSS_SELECTOR, "a[href*='/opportunities/']")
                if links:
                    url = links[0].get_attribute("href")
        return details, url

    # -- orchestration ------------------------------------------------------

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        self._save_run_row()  # initial run row (best-effort)
        try:
            self.start_driver()
            self.login()
            self.open_opportunities()
            self.scrape_opportunities()

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
