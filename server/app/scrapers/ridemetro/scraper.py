"""Selenium automation for the RideMetro account's Euna Supplier Network.

Flow: log in to the Bonfire portal -> click "My Euna Supplier Network" -> switch
the top nav from Dashboard to "My Network" -> for every agency whose Status is
Complete, click "Go to Agency" and read its Open Public Opportunities list ->
store everything in the DB -> generate one agency-grouped Excel report.

Agencies whose Status is Incomplete are skipped: the supplier registration for
them is unfinished, and their "Go to Agency" button points at that agency's
/registration page rather than at a portal with opportunities on it.

We deliberately do NOT open individual opportunity pages or download documents:
the /opportunities/* pages sit behind a Cloudflare "verify you are human"
challenge that manual browsing avoids but automation trips, and repeatedly
solving it on a real vendor account is risky. Everything we export is read from
the opportunities lists, which load cleanly. To pull an opportunity's documents,
open its "Bid URL" in a browser and download them by hand.
"""

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC

from app.config import settings
from app.core import run_manager
from app.core.base_scraper import BaseScraper, StopRequested
from app.core.closing_filter import MIN_DAYS_UNTIL_CLOSE, days_until_close
from app.core.exports import archive_run
from app.core.filenames import timestamp
from app.scrapers.ridemetro import export, network, opportunities
from app.scrapers.ridemetro.network import Agency
from app.services.notifier import notify_scrape_completion

logger = logging.getLogger(__name__)

SEL = {
    "login_email": (By.CSS_SELECTOR, "input[name='email'], #input-email, input[type='email']"),
    "login_password": (By.CSS_SELECTOR, "input[name='password'], #input-password, input[type='password']"),
    "login_submit": (By.CSS_SELECTOR, "button[type='submit']"),
}

# An agency portal's opportunities list, relative to the agency's own host. The
# "Go to Agency" button lands on the portal index already; this is the fallback
# for an agency whose root doesn't redirect there.
PORTAL_PATH = "/portal/?tab=openOpportunities"

# How long to wait for the browser tab that "Go to Agency" opens.
NEW_TAB_TIMEOUT = 30


class RideMetroScraper(BaseScraper):
    def __init__(self, run_id: str):
        super().__init__(run_id)
        self.excel_path: Path | None = None
        self._records: list[dict[str, Any]] = []
        self._agencies: list[Agency] = []
        # The Supplier Network tab we return to between agencies.
        self._network_handle: str | None = None
        # Opportunities whose Close Date is inside MIN_DAYS_UNTIL_CLOSE. Reported,
        # not dropped: the brief is every open public opportunity, and the sheet
        # carries Days Left so a reader can apply their own cut-off.
        self._closing_soon = 0

    # -- flow steps ---------------------------------------------------------

    def login(self) -> None:
        self.set_step("logging_in")
        self.navigate(settings.ridemetro_login_url)
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

    def open_supplier_network(self) -> None:
        """Click "My Euna Supplier Network" and follow it to vendor.bonfirehub.com.

        The button carries target="_blank", so it opens a tab; we adopt that tab
        as the one the rest of the run works in. If the button isn't on the page
        (the portal only renders it for accounts that have a supplier network,
        behind a feature flag) we navigate to the network directly — the session
        cookie is shared across the bonfirehub domains, so the destination is the
        same either way.
        """
        self.set_step("opening_supplier_network")
        self.navigate(settings.ridemetro_opportunities_url)
        try:
            button = self.wait(30).until(
                EC.element_to_be_clickable(network.SEL["supplier_network_button"])
            )
        except TimeoutException:
            logger.warning(
                "[run %s] no 'My Euna Supplier Network' button on the portal — "
                "going straight to %s", self.run_id, settings.ridemetro_supplier_network_url,
            )
            self.navigate(settings.ridemetro_supplier_network_url)
        else:
            handle = self._click_into_new_tab(button)
            if handle:
                self.driver.switch_to.window(handle)
            else:
                self.navigate(settings.ridemetro_supplier_network_url)

        self.wait(60).until(lambda d: "vendor.bonfirehub.com" in d.current_url)
        self._network_handle = self.driver.current_window_handle
        logger.info("[run %s] supplier network: %s", self.run_id, self.driver.current_url)

    def open_my_network(self) -> None:
        """Switch the top nav from Dashboard (the default) to My Network."""
        self.set_step("opening_my_network")
        try:
            tab = self.wait(30).until(EC.element_to_be_clickable(network.SEL["my_network_tab"]))
            self.scroll_into_view(tab)
            tab.click()
        except (TimeoutException, WebDriverException):
            logger.warning("[run %s] My Network tab not clickable — navigating to it", self.run_id)
            self.navigate(settings.ridemetro_agencies_url)

        # The roster renders client-side after the tab switch.
        self.wait(60).until(
            EC.presence_of_element_located(network.SEL["go_to_agency"])
        )

    def collect_agencies(self) -> None:
        """Read the My Network roster and record which agencies will be visited."""
        self.set_step("reading_my_network")
        self._agencies = network.read_agencies(self.driver)

        # The Active tab states how many agencies there are. A shorter roster
        # means the list was still filling in — wait once and re-read rather than
        # sweeping a subset and calling it the whole network.
        expected = network.active_count(self.driver)
        if expected is not None and len(self._agencies) < expected:
            time.sleep(3)
            self._agencies = network.read_agencies(self.driver)
        if expected is not None and len(self._agencies) < expected:
            message = (
                f"My Network lists {expected} active agencies but only "
                f"{len(self._agencies)} could be read — the rest were not scraped."
            )
            logger.warning("[run %s] %s", self.run_id, message)
            run_manager.add_warning(self.run_id, message)

        complete = [a for a in self._agencies if a.is_complete]
        logger.info(
            "[run %s] My Network: %d agencies, %d Complete, %d skipped as Incomplete",
            self.run_id, len(self._agencies), len(complete),
            len(self._agencies) - len(complete),
        )
        for agency in self._agencies:
            logger.info(
                "[run %s]   %-55s %-12s %s",
                self.run_id, agency.name[:55], agency.status or "(no status)",
                "-> scraping" if agency.is_complete else "-> skipped",
            )
        self._report_agencies()

    def scrape_network(self) -> None:
        """Visit every Complete agency and read its Open Public Opportunities."""
        scraped = 0
        for index, agency in enumerate(self._agencies, start=1):
            self.raise_if_stopped()
            if not agency.is_complete:
                continue
            self.set_step(f"scraping_agency ({index}/{len(self._agencies)}): {agency.name}")
            try:
                self._scrape_agency(agency)
                scraped += 1
            except StopRequested:
                raise
            except Exception as exc:  # noqa: BLE001 — one agency must not sink the sweep
                agency.error = self.describe_failure(exc)
                logger.exception("[run %s] agency %s failed", self.run_id, agency.name)
                run_manager.add_error(self.run_id, f"{agency.name}: {agency.error}")
                self.screenshot(f"agency_{agency.name}")
                self._return_to_network()
            self._report_agencies(scraped)
        self._report_agencies(scraped)

    def _scrape_agency(self, agency: Agency) -> None:
        """Open one agency's portal in its own tab and read the open list."""
        opened_tab = self._open_agency(agency)
        try:
            self._await_opportunities(agency)
            records = opportunities.read_rows(self.driver)
            for details in records:
                details["agency"] = agency.name
                details["agency_url"] = agency.url
                details["zip_filename"] = None
                self._records.append(details)

                days = days_until_close(details.get("close_date"))
                if days is not None and days < MIN_DAYS_UNTIL_CLOSE:
                    self._closing_soon += 1

                run_manager.add_bid_result(self.run_id, {
                    "agency": agency.name,
                    "ref_number": details.get("ref_number"),
                    "project": details.get("project"),
                    "close_date": details.get("close_date"),
                    "days_left": details.get("days_left"),
                    "documents": [],
                    "error": None,
                })
            agency.opportunities = len(records)
            run_manager.update_run(self.run_id, bids_found=len(self._records))
            logger.info(
                "[run %s] %s: %d open opportunit%s",
                self.run_id, agency.name, len(records), "y" if len(records) == 1 else "ies",
            )
        finally:
            if opened_tab:
                self._close_tab()
            self._return_to_network()

    def _open_agency(self, agency: Agency) -> bool:
        """Click the agency's "Go to Agency" button; return True if a tab opened.

        The button is an `<a target="_blank">`, so the normal outcome is a new
        tab with the My Network list left untouched behind it — which is what
        keeps the roster's elements alive across the sweep. When the click is
        swallowed (a re-render between reading the roster and clicking it) we
        fall back to opening the agency's URL ourselves, in this same tab.
        """
        try:
            button = self.wait(20).until(
                EC.element_to_be_clickable(network.go_to_selector(agency.name))
            )
        except TimeoutException:
            logger.warning(
                "[run %s] no 'Go to Agency' button for %s any more — opening %s directly",
                self.run_id, agency.name, agency.url,
            )
            self.navigate(agency.url)
            return False

        handle = self._click_into_new_tab(button)
        if handle:
            self.driver.switch_to.window(handle)
            return True
        logger.warning(
            "[run %s] 'Go to Agency' for %s opened no tab — navigating to %s",
            self.run_id, agency.name, agency.url,
        )
        self.navigate(agency.url)
        return False

    def _await_opportunities(self, agency: Agency) -> None:
        """Wait for the agency's Open Public Opportunities list to be readable.

        "Go to Agency" lands on the portal index with the open tab already
        selected; we click the tab anyway (belt and braces) and wait for its
        pane's rows. An agency with nothing open still renders a placeholder
        row, so this doesn't hang on an empty portal. If the landing page isn't
        the portal at all, we go to the portal path on that agency's host.
        """
        for attempt in (1, 2):
            try:
                self.wait(30).until(EC.presence_of_element_located(opportunities.SEL["pane"]))
                break
            except TimeoutException:
                if attempt == 2:
                    raise
                target = urljoin(self.driver.current_url or agency.url, PORTAL_PATH)
                logger.info(
                    "[run %s] %s did not land on the portal (%s) — trying %s",
                    self.run_id, agency.name, self.driver.current_url, target,
                )
                self.navigate(target)

        try:
            self.wait(15).until(
                EC.element_to_be_clickable(opportunities.SEL["tab_link"])
            ).click()
        except (TimeoutException, WebDriverException):
            pass  # the tab is active on load; the click is only a safeguard

        # The pane's table is filled by an AJAX call that fires after the sibling
        # tabs' calls resolve, so this is the wait that actually matters.
        self.wait(60).until(EC.presence_of_element_located(opportunities.SEL["rows"]))

    # -- browser tab plumbing -----------------------------------------------

    def _click_into_new_tab(self, element) -> str | None:
        """Click a target="_blank" link and return the handle it opened, if any."""
        before = set(self.driver.window_handles)
        self.scroll_into_view(element)
        try:
            element.click()
        except WebDriverException:
            # An overlay (cookie bar, sticky header) can intercept the click;
            # the DOM-level click is not subject to hit-testing.
            self.driver.execute_script("arguments[0].click();", element)

        deadline = time.monotonic() + NEW_TAB_TIMEOUT
        while time.monotonic() < deadline:
            self.raise_if_stopped()
            opened = set(self.driver.window_handles) - before
            if opened:
                return opened.pop()
            time.sleep(0.25)
        return None

    def _close_tab(self) -> None:
        try:
            self.driver.close()
        except WebDriverException:
            logger.debug("[run %s] agency tab was already gone", self.run_id, exc_info=True)

    def _return_to_network(self) -> None:
        """Come back to the My Network list, whatever state the sweep left us in.

        Any stray agency tabs are closed first so a long sweep doesn't
        accumulate one browser tab per agency.
        """
        if not self._network_handle:
            return
        try:
            for handle in self.driver.window_handles:
                if handle != self._network_handle:
                    self.driver.switch_to.window(handle)
                    self.driver.close()
            self.driver.switch_to.window(self._network_handle)
        except WebDriverException:
            logger.warning("[run %s] lost the My Network tab — reopening it", self.run_id)
            self.navigate(settings.ridemetro_agencies_url)
            self._network_handle = self.driver.current_window_handle
            self.wait(60).until(EC.presence_of_element_located(network.SEL["go_to_agency"]))
            return

        # A tab switch alone doesn't guarantee the roster is still mounted (the
        # SPA re-renders on focus); make sure the buttons are there before the
        # next agency is looked up.
        try:
            self.wait(20).until(EC.presence_of_element_located(network.SEL["go_to_agency"]))
        except TimeoutException:
            self.navigate(settings.ridemetro_agencies_url)
            self.wait(60).until(EC.presence_of_element_located(network.SEL["go_to_agency"]))

    # -- reporting ----------------------------------------------------------

    def _report_agencies(self, scraped: int | None = None) -> None:
        """Publish the roster (and its progress) onto the run."""
        fields: dict[str, Any] = {
            "agencies": [a.as_record() for a in self._agencies],
            "agencies_found": len(self._agencies),
        }
        if scraped is not None:
            fields["agencies_scraped"] = scraped
        run_manager.update_run(self.run_id, **fields)

    # -- orchestration ------------------------------------------------------

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        self._save_run_row()  # initial run row (best-effort)
        try:
            self.start_driver()
            self.login()
            self.open_supplier_network()
            self.open_my_network()
            self.collect_agencies()
            self.scrape_network()

            complete = sum(1 for a in self._agencies if a.is_complete)
            run_manager.update_run(
                self.run_id,
                bids_found=len(self._records),
                no_results=not self._records,
                bids_closing_soon=self._closing_soon,
            )
            logger.info(
                "[run %s] swept %d/%d agencies: %d opportunities (%d close within %sd)",
                self.run_id, complete, len(self._agencies), len(self._records),
                self._closing_soon, MIN_DAYS_UNTIL_CLOSE,
            )
            if self._closing_soon:
                run_manager.add_warning(
                    self.run_id,
                    f"{self._closing_soon} of these close within {MIN_DAYS_UNTIL_CLOSE} days "
                    f"— see the Days Left column.",
                )

            # Persist every scraped opportunity in one transaction. Best-effort:
            # a DB failure must not fail the run — the report is then written
            # straight from the in-memory records.
            run = run_manager.get_run(self.run_id) or {"run_id": self.run_id}
            db_ok = True
            try:
                stored = export.save_bids(run, self._records)
                run_manager.update_run(self.run_id, bids_stored_in_db=stored)
            except Exception:  # noqa: BLE001 — DB issues shouldn't abort the run
                db_ok = False
                logger.exception("[run %s] DB save failed", self.run_id)
                run_manager.add_error(self.run_id, "db save failed (see logs)")
                run_manager.update_run(self.run_id, db_save_failed=True)

            self.set_step("generating_excel")
            if db_ok:
                # No Excel is written to disk here — the report is rebuilt from
                # the DB on demand (Download button / completion email).
                run_manager.update_run(self.run_id, excel_exported=True)
            else:
                # DB outage: the records exist only in memory, so a disk report is
                # the only copy the download/email can serve.
                try:
                    label = (run_manager.get_run(self.run_id) or {}).get("label") or timestamp()
                    self.excel_path = self.run_dir / f"RideMetro_Bids ({label}) [{self.run_id}].xlsx"
                    export.generate_excel_from_records(
                        self._records, self.excel_path,
                        [a.as_record() for a in self._agencies],
                    )
                    run_manager.update_run(
                        self.run_id, excel_path=str(self.excel_path), excel_exported=True
                    )
                except Exception:  # noqa: BLE001 — never fail the run over the Excel
                    logger.exception("[run %s] Excel generation failed", self.run_id)
                    run_manager.add_error(self.run_id, "excel generation failed (see logs)")

            # Package the run's deliverable (for RideMetro, the bare report —
            # the sweep downloads no documents) and delete the workspace.
            self.set_step("packaging_results")
            archive_run(self.run_id)

            run_manager.update_run(self.run_id, status="completed", step="done")
            notify_scrape_completion(self.run_id, "ridemetro", len(self._records))
        except Exception as exc:  # noqa: BLE001 — a failed run must be reported, not crash the worker
            logger.exception("[run %s] failed", self.run_id)
            self.screenshot("fatal")
            run_manager.add_error(self.run_id, self.describe_failure(exc))
            run_manager.update_run(self.run_id, status="failed", step="failed")
        finally:
            self.cleanup()
            run_manager.update_run(self.run_id, finished_at=datetime.now().isoformat())
            self._save_run_row()  # final counts (best-effort)
            run_manager.remove_empty_folder(self.run_id)

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
