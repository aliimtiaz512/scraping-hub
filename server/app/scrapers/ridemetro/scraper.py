"""Selenium automation for a RideMetro account's Euna Supplier Network.

Flow: log in to the Bonfire portal -> click "My Euna Supplier Network" -> switch
the top nav from Dashboard to "My Network" -> for every agency whose Status is
Complete, click "Go to Agency" and read its Open Public Opportunities list ->
store everything in the DB -> generate one agency-grouped Excel report.

A run signs in as one of two configured accounts (see `accounts`), chosen when
the run is started. That choice only decides which credentials the login step
types: each account belongs to a different supplier network, so the same flow
then sweeps whichever agencies that network lists.

Agencies whose Status is Incomplete are skipped: the supplier registration for
them is unfinished, and their "Go to Agency" control points at that agency's
/registration page rather than at a portal with opportunities on it. That is the
only reason an agency is left out — the roster itself is read whole, and the
Active tab's count is what the run reconciles it against.

"Go to Agency" is an `<a target="_blank">` for most agencies and a plain
`<button>` for the rest (see `network`), so opening one lands either in a new
tab or in this one. `_open_agency` says which, and that is what decides whether
the cleanup closes a tab or reopens My Network.

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

from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC

from app.config import settings
from app.core import partials, run_manager
from app.core.base_scraper import BaseScraper, StopRequested
from app.core.closing_filter import MIN_DAYS_UNTIL_CLOSE, days_until_close
from app.core.exports import archive_run
from app.core.filenames import timestamp
from app.scrapers.ridemetro import accounts, export, network, opportunities
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

# Where "Go to Agency" lands for an agency the SPA has no portal URL for. It is
# the region's session-scoped portal rather than any one agency's: it opens on
# My Opportunities — the things this vendor account was invited to — and has no
# Open Public Opportunities tab, because no agency is in scope to have a public
# list. Every agency whose control is a button (see `network`) is in this
# position, which is *why* the control is a button and not a link.
SESSION_HOSTS = ("session.bonfirehub.com", "session.bonfirehub.ca")

# How long to wait for the browser tab that "Go to Agency" opens.
NEW_TAB_TIMEOUT = 30

# Bonfire renders its portal client-side, and the driver is configured to return
# from a navigation at DOMContentLoaded (`page_load_strategy = "eager"` in
# BaseScraper) — so "the page loaded" happens well before the portal exists, and
# the opportunities themselves arrive later still, over their own AJAX call
# after the sibling tabs' calls resolve. Three separate budgets, because they
# are three separate waits and a single number for all of them either fails the
# slow ones or hides a stall behind a long hang.
PANE_TIMEOUT = 45        # server markup + SPA boot: the pane element appears
HYDRATION_TIMEOUT = 60   # the open list's AJAX call resolves and rows render
HYDRATION_POLL_S = 1.0   # how often to re-check while it is still working
# How long a built, idle, empty table has to stay that way before we believe it.
# Some portals render no "no open projects" placeholder at all, so the only
# evidence they have finished is that they have stopped changing; a few seconds
# of stillness distinguishes that from the gap between the table being built and
# its first fetch starting.
IDLE_SETTLE_POLLS = 4

# How many times the roster is re-read when it comes back shorter than the
# Active tab's count. The list is client-side, so a short read is usually a
# render that had not finished, and re-reading costs a second.
ROSTER_READ_ATTEMPTS = 4
ROSTER_READ_PAUSE = 3

# Attempts per agency. A portal that times out or lands somewhere unexpected
# gets one more go from a freshly reloaded roster before its row is written off.
AGENCY_ATTEMPTS = 2

# What `_open_agency` did, which is what tells `_scrape_agency` how to clean up.
NEW_TAB = "new_tab"    # the agency opened beside My Network; close it afterwards
SAME_TAB = "same_tab"  # we navigated this tab; My Network has to be reopened


class NoOpportunitiesPortal(Exception):
    """"Go to Agency" worked, but where it lands is not an opportunities portal.

    My Network lists every organisation the account has touched, and not all of
    them publish a public portal — the platform's own organisation is one such
    row. That is a standing fact about the agency, not a fault in the run: the
    click lands in the same place every time, so there is nothing to retry and
    nothing to fix, and the report should say so instead of carrying a
    chromedriver stack trace under "could not be read".
    """


class RideMetroScraper(BaseScraper):
    def __init__(self, run_id: str):
        super().__init__(run_id)
        # Which login this run uses. Resolved (and its credentials checked) in
        # run(), so a misconfigured account fails the run with a clear reason
        # instead of at the portal's login screen.
        self.account: accounts.Account | None = None
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
        """Sign in as this run's account. The sequence is the same either way —
        only the credentials typed into it differ."""
        account = self.account or accounts.get(None)
        self.set_step("logging_in")
        self.navigate(settings.ridemetro_login_url)
        # Bonfire/Euna uses an identifier-first flow: the first screen shows only
        # the email field and a "Continue" button. The password field is not in
        # the DOM until the email is submitted, so we fill and submit email first,
        # then wait for the password field to appear before filling it.
        email = self.wait().until(EC.element_to_be_clickable(SEL["login_email"]))
        email.clear()
        email.send_keys(account.username)
        self.driver.find_element(*SEL["login_submit"]).click()

        password = self.wait().until(EC.element_to_be_clickable(SEL["login_password"]))
        password.clear()
        password.send_keys(account.password)

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

        # The Active tab states how many cards the panel holds — every card,
        # Incomplete ones included. It is the only number on the page that says
        # how long the roster is meant to be, so it is what a short read is
        # reconciled against: re-read until the two agree rather than sweeping a
        # subset and calling it the whole network.
        expected = network.active_count(self.driver)
        self._agencies = network.read_agencies(self.driver)
        for attempt in range(2, ROSTER_READ_ATTEMPTS + 1):
            if expected is None or len(self._agencies) >= expected:
                break
            logger.info(
                "[run %s] My Network shows %d agencies but only %d have rendered — "
                "re-reading (attempt %d/%d)",
                self.run_id, expected, len(self._agencies), attempt, ROSTER_READ_ATTEMPTS,
            )
            self.raise_if_stopped()
            time.sleep(ROSTER_READ_PAUSE)
            self._agencies = network.read_agencies(self.driver)

        if expected is not None and len(self._agencies) < expected:
            message = (
                f"My Network lists {expected} agencies but only "
                f"{len(self._agencies)} could be read — the rest were not scraped."
            )
            logger.warning("[run %s] %s", self.run_id, message)
            run_manager.add_warning(self.run_id, message)

        complete = [a for a in self._agencies if a.is_complete]
        logger.info(
            "[run %s] My Network: %d agencies listed%s, %d Complete, %d skipped",
            self.run_id, len(self._agencies),
            f" (Active tab says {expected})" if expected is not None else "",
            len(complete), len(self._agencies) - len(complete),
        )
        for agency in self._agencies:
            logger.info(
                "[run %s]   %-14s %-55s %-12s %s",
                self.run_id, agency.agency_id or "-", agency.name[:55],
                agency.status or "(no status)",
                "-> scraping" if agency.is_complete else "-> skipped",
            )
        self._report_agencies()

    def scrape_network(self) -> None:
        """Visit every Complete agency and read its Open Public Opportunities.

        The queue is built up front, by status, so what will be visited is
        decided once and stated in the log — not rediscovered inside the loop
        where a mid-sweep re-render could change it.
        """
        queue = []
        for agency in self._agencies:
            if agency.is_complete:
                queue.append(agency)
            elif agency.is_incomplete:
                logger.info(
                    "[run %s] Skipping agency %s due to INCOMPLETE status",
                    self.run_id, agency.label,
                )
            else:
                # Not the portal telling us to skip it — we could not read the
                # field. Skipped the same way, but said out loud, because it
                # means the card's markup moved.
                logger.warning(
                    "[run %s] Skipping agency %s: unreadable status %r",
                    self.run_id, agency.label, agency.status,
                )

        logger.info(
            "[run %s] sweeping %d of %d agencies", self.run_id, len(queue), len(self._agencies),
        )

        scraped = 0
        for index, agency in enumerate(queue, start=1):
            self.raise_if_stopped()
            self.set_step(f"scraping_agency ({index}/{len(queue)}): {agency.name}")
            # Each agency is isolated: a timeout, a portal that never renders or
            # a control that vanished costs that agency's rows and nothing else.
            for attempt in range(1, AGENCY_ATTEMPTS + 1):
                # Where this agency's rows start, so a failed attempt can be
                # rolled back: an agency that fails half way through recording
                # its list would otherwise have those rows counted twice once
                # the retry read the same list again.
                mark, closing_soon = len(self._records), self._closing_soon
                try:
                    self._scrape_agency(agency)
                    agency.error = None
                    scraped += 1
                    break
                except StopRequested:
                    raise
                except NoOpportunitiesPortal as exc:
                    # Settled, not failed: clicking again lands in the same
                    # place, so this agency is done and the sweep moves on.
                    del self._records[mark:]
                    self._closing_soon = closing_soon
                    agency.opportunities = 0
                    agency.error = None
                    agency.note = export.NOTE_NO_PORTAL
                    run_manager.update_run(self.run_id, bids_found=len(self._records))
                    logger.warning(
                        "[run %s] agency %s has no opportunities portal: %s",
                        self.run_id, agency.label, exc,
                    )
                    run_manager.add_warning(self.run_id, f"{agency.name}: {export.NOTE_NO_PORTAL}")
                    self._recover_to_network()
                    break
                except Exception as exc:  # noqa: BLE001 — one agency must not sink the sweep
                    agency.error = self.describe_failure(exc)
                    del self._records[mark:]
                    self._closing_soon = closing_soon
                    agency.opportunities = 0
                    run_manager.update_run(self.run_id, bids_found=len(self._records))
                    if attempt < AGENCY_ATTEMPTS:
                        logger.warning(
                            "[run %s] agency %s failed (%s) — retrying %d/%d",
                            self.run_id, agency.label, agency.error, attempt + 1, AGENCY_ATTEMPTS,
                        )
                        self._recover_to_network()
                        continue
                    logger.exception("[run %s] agency %s failed", self.run_id, agency.label)
                    run_manager.add_error(self.run_id, f"{agency.name}: {agency.error}")
                    self.screenshot(f"agency_{agency.name}")
                    self._recover_to_network()
            self._report_agencies(scraped)
        self._report_agencies(scraped)

    def _recover_to_network(self) -> None:
        """Get back to My Network after a failure, without raising a new one.

        Recovery that throws would end the sweep at the first bad agency, which
        is the failure mode this whole loop exists to prevent.
        """
        try:
            self._return_to_network()
        except StopRequested:
            raise
        except Exception:  # noqa: BLE001
            logger.warning(
                "[run %s] could not get back to My Network — the next agency will "
                "reopen it", self.run_id, exc_info=True,
            )

    def _scrape_agency(self, agency: Agency) -> None:
        """Open one agency's portal in its own tab and read the open list."""
        opened = self._open_agency(agency)
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
            if opened == NEW_TAB:
                self._close_tab()
            # Getting back to the roster is cleanup, not part of reading this
            # agency: letting it throw here would mark an agency whose rows are
            # already captured as one that "could not be read", which is what
            # the report would then say. The next agency's lookup re-navigates
            # to My Network anyway if the roster really is gone.
            self._recover_to_network()

    def _open_agency(self, agency: Agency) -> str:
        """Click the agency's "Go to Agency" control; say which tab it landed in.

        The control comes in two shapes and both are normal:

        * an `<a target="_blank">`, which opens a new tab and leaves the My
          Network list mounted behind it — that is what keeps the roster's
          elements alive across the sweep, so it is the preferred outcome; and
        * a `<button>` with no href, which the SPA handles by navigating *this*
          tab. Those agencies have no URL to fall back to, so clicking is the
          only way in and the roster has to be reopened afterwards.

        Returns NEW_TAB or SAME_TAB accordingly. Raises when the agency cannot
        be reached at all, which the sweep records against that agency alone.

        The control is looked up again on a stale reference rather than given up
        on: this list is a React component that re-renders on focus, and on a
        network with a dozen agencies it does so often enough that a single
        stale element would otherwise cost that agency's whole opportunity list.
        """
        button = None
        for attempt in (1, 2):
            try:
                button = self.wait(20).until(
                    EC.element_to_be_clickable(network.go_to_selector(agency.name))
                )
                break
            except StaleElementReferenceException:
                logger.info(
                    "[run %s] the My Network list re-rendered while opening %s — "
                    "looking the button up again", self.run_id, agency.label,
                )
            except TimeoutException:
                break

        if button is None:
            logger.warning(
                "[run %s] no 'Go to Agency' control for %s any more",
                self.run_id, agency.label,
            )
            return self._open_agency_url(agency)

        try:
            handle = self._click_into_new_tab(button, watch_current_tab=True)
        except StaleElementReferenceException:
            # It went stale between the lookup and the click. For a linked
            # agency the href is the same thing the control would have opened.
            logger.warning(
                "[run %s] 'Go to Agency' for %s went stale on click",
                self.run_id, agency.label,
            )
            return self._open_agency_url(agency)

        if handle:
            self.driver.switch_to.window(handle)
            return NEW_TAB

        # No new tab. For a button-rendered agency that is the expected path:
        # the click navigated the tab we are standing in.
        if self._left_the_network():
            logger.info(
                "[run %s] %s opened in place at %s",
                self.run_id, agency.label, self.driver.current_url,
            )
            return SAME_TAB

        logger.warning(
            "[run %s] 'Go to Agency' for %s did nothing", self.run_id, agency.label,
        )
        return self._open_agency_url(agency)

    def _open_agency_url(self, agency: Agency) -> str:
        """Last resort: go to the agency's own URL in this tab.

        A button-rendered agency has no URL — the portal never put one in the
        DOM — so there is nothing to fall back to and saying so is better than
        navigating to an empty string and reporting whatever loads.
        """
        if not agency.url:
            raise RuntimeError(
                "no 'Go to Agency' control on My Network and no direct URL for "
                f"{agency.label}"
            )
        logger.info("[run %s] opening %s directly", self.run_id, agency.url)
        self.navigate(agency.url)
        return SAME_TAB

    def _on_session_portal(self) -> bool:
        """Are we on the region's shared session portal rather than an agency's?"""
        try:
            url = self.driver.current_url or ""
        except WebDriverException:
            return False
        return any(host in url for host in SESSION_HOSTS)

    def _left_the_network(self) -> bool:
        """Is the current tab somewhere other than the supplier network?"""
        try:
            return "vendor.bonfirehub.com" not in (self.driver.current_url or "")
        except WebDriverException:
            return False

    def _await_opportunities(self, agency: Agency) -> None:
        """Wait for the agency's Open Public Opportunities list to be readable.

        Three things have to be true before the list can be read, and they fail
        in different ways, so they are waited on separately.

        *The open list has to be reachable.* On most agencies "Go to Agency"
        lands on the portal index with the open tab already selected. Some land
        on a different tab, and some land on a portal that has no open tab at
        all — the region session portal, which is where an agency with no portal
        URL of its own sends you. So the wait is for the pane *or* the tab, and
        a portal offering neither is a settled fact rather than a slow page.

        *The right tab has to be selected.* Clicking it is cheap and idempotent
        when it is already active.

        *The list has to have rendered* — which is not the same as existing; see
        `_await_hydrated_rows`.
        """
        for attempt in (1, 2):
            try:
                # Either is enough to proceed: the pane means the open list is
                # already selected, the tab means it can be. Waiting on the pane
                # alone spent the whole budget on portals that open on a
                # different tab, and the whole budget again on portals that have
                # no public list at all and never could show one.
                self.wait(PANE_TIMEOUT).until(
                    EC.any_of(
                        EC.presence_of_element_located(opportunities.SEL["pane"]),
                        EC.presence_of_element_located(opportunities.SEL["open_tab"]),
                    )
                )
                break
            except TimeoutException:
                # The fallback rewrites the tab on whatever host we are on. On
                # the session host that is pointless — no path there carries an
                # agency's list — so decide now rather than spending the budget
                # twice to reach the same conclusion.
                if attempt == 2 or self._on_session_portal():
                    raise self._diagnose_missing_pane(agency) from None
                target = urljoin(self.driver.current_url or agency.url, PORTAL_PATH)
                logger.info(
                    "[run %s] %s did not land on the open list (%s) — trying %s",
                    self.run_id, agency.label, self.driver.current_url, target,
                )
                self.navigate(target)

        try:
            self.wait(15).until(
                EC.element_to_be_clickable(opportunities.SEL["tab_link"])
            ).click()
        except (TimeoutException, WebDriverException):
            pass  # the tab is active on load; the click is only a safeguard

        # We may have got this far on the tab alone — the pane is built when the
        # tab is selected, so it can still be a moment away.
        try:
            self.wait(PANE_TIMEOUT).until(
                EC.presence_of_element_located(opportunities.SEL["pane"])
            )
        except TimeoutException:
            raise self._diagnose_missing_pane(agency) from None

        self._await_hydrated_rows(agency)

    def _await_hydrated_rows(self, agency: Agency) -> None:
        """Wait until the open list has actually rendered, not merely appeared.

        Waiting on the presence of a row was the bug this replaces. DataTables
        puts a row into the table the instant it initialises, long before the
        opportunities arrive, and that row carries neither a reference nor a
        link — so `read_rows` discarded it as the "no open projects" placeholder
        and the agency was reported as having nothing. An agency with live bids
        came back empty, with no error anywhere: the wait was satisfied, the
        read succeeded, and the answer was wrong.

        So the condition is the *content* of the table, not its existence, and
        the loop keeps checking until the page says one of two things: a real
        opportunity row, or the portal's own settled empty-state text.
        """
        deadline = time.monotonic() + HYDRATION_TIMEOUT
        polls = 0
        idle_polls = 0

        while time.monotonic() < deadline:
            self.raise_if_stopped()
            state = opportunities.hydration_state(self.driver)

            if state == opportunities.READY:
                if polls:
                    logger.info(
                        "[run %s] %s: open list hydrated after %.0fs",
                        self.run_id, agency.label, polls * HYDRATION_POLL_S,
                    )
                return

            if state == opportunities.EMPTY:
                # The portal said so itself.
                logger.info(
                    "[run %s] %s: no open opportunities — recording 0 bids",
                    self.run_id, agency.label,
                )
                return

            if state == opportunities.IDLE:
                # Built, not fetching, and holding nothing — but not saying so.
                # Believe it once it has held still, rather than spending the
                # whole budget waiting for a placeholder that is never coming.
                idle_polls += 1
                if idle_polls >= IDLE_SETTLE_POLLS:
                    logger.info(
                        "[run %s] %s: open list has been empty and idle for %.0fs "
                        "with no placeholder — recording 0 bids",
                        self.run_id, agency.label, idle_polls * HYDRATION_POLL_S,
                    )
                    return
            else:
                idle_polls = 0  # something started happening again

            polls += 1
            time.sleep(HYDRATION_POLL_S)

        self._resolve_hydration_timeout(agency)

    def _resolve_hydration_timeout(self, agency: Agency) -> None:
        """Decide what a hydration deadline actually means.

        Two things end up here and only one of them is a failure. A table that
        is still fetching after the full budget is a stall worth reporting and
        retrying. A table that is simply sitting there, idle and rowless — an
        agency whose portal renders no placeholder at all — has finished; it has
        nothing, and failing the agency over that would lose the other twenty.
        """
        state = opportunities.hydration_state(self.driver)
        has_table = bool(self.driver.find_elements(*opportunities.SEL["table"]))

        # An idle table only reaches the deadline if it kept flickering in and
        # out of idle for the whole budget — the settle check above returns
        # long before this on a portal that simply sits still. Reading it as
        # empty is still the right call; it just deserves saying out loud.
        if state == opportunities.IDLE:
            logger.warning(
                "[run %s] %s: open list never settled in %ds but is idle and "
                "empty — recording 0 bids",
                self.run_id, agency.label, HYDRATION_TIMEOUT,
            )
            return

        where = self.driver.current_url or "(unknown)"
        detail = (
            f"the open list did not finish rendering in {HYDRATION_TIMEOUT}s at {where}"
        )
        if not has_table:
            detail += " — the pane is there but its table was never built"
        if opportunities.has_frames(self.driver):
            # Not a case seen on any portal so far; named explicitly because a
            # table moved inside a frame would otherwise look like a plain stall.
            detail += " — note the page embeds a frame, which the open list may have moved into"
        if state == opportunities.LOADING:
            detail += " — it is still fetching"
        raise TimeoutException(detail)

    def _diagnose_missing_pane(self, agency: Agency) -> Exception:
        """Turn "the pane selector timed out" into something a log can act on.

        A bare TimeoutException names a CSS selector and a chromedriver stack
        and says nothing about where the browser actually ended up. Two very
        different situations produce it, and they want opposite handling: a page
        that carries the portal's tab chrome but whose pane did not render is
        worth another attempt, while a page with no portal on it at all will
        land identically however many times it is clicked.
        """
        try:
            url = self.driver.current_url or "(unknown)"
        except WebDriverException:
            url = "(unreachable)"
        try:
            title = (self.driver.title or "").strip()
        except WebDriverException:
            title = ""
        where = f"{url}{f' — {title}' if title else ''}"

        try:
            on_a_portal = bool(self.driver.find_elements(*opportunities.SEL["portal_markers"]))
        except WebDriverException:
            on_a_portal = True  # can't tell; treat it as the retryable case

        if not on_a_portal:
            return NoOpportunitiesPortal(
                f"'Go to Agency' lands on {where}, which is not an opportunities portal"
            )

        # It is a portal — but not necessarily one with a public list. A portal
        # that renders tabs and no Open Public Opportunities among them has
        # nothing to show us and will land identically however long we wait, so
        # it is settled rather than slow. That is the shape of the region
        # session portal, which is where every agency the SPA has no portal URL
        # for ends up.
        try:
            has_open_tab = opportunities.has_open_tab(self.driver)
            tabs = opportunities.offered_tabs(self.driver)
        except WebDriverException:
            has_open_tab, tabs = True, []

        # The session portal is shared across every agency the SPA has no portal
        # URL for, so whatever it lists is not this agency's — reading it would
        # attribute one shared list to several agencies. That makes it a settled
        # verdict on the host alone, whatever tabs it happens to render.
        if any(host in url for host in SESSION_HOSTS):
            return NoOpportunitiesPortal(
                f"'Go to Agency' lands on {where} — the region session portal. Its "
                f"list is shared across agencies rather than scoped to this one, "
                f"so nothing there can be attributed to {agency.name}"
            )

        # Otherwise "no open tab" is only a verdict if the portal's tab chrome
        # actually rendered. With no tabs at all we cannot tell a portal that has
        # no public list from one that had not finished drawing when the wait ran
        # out — and calling the second one settled would drop a real agency's
        # bids with no retry and no error.
        if not has_open_tab and tabs:
            return NoOpportunitiesPortal(
                f"'Go to Agency' lands on {where}, a portal with no public "
                f"opportunities list: it has no Open Public Opportunities tab, "
                f"and offers {', '.join(tabs)}"
            )

        detail = f"the Open Public Opportunities pane never rendered at {where}"
        try:
            if opportunities.has_frames(self.driver):
                detail += (
                    " — the page embeds a frame; if the portal has moved its "
                    "table inside one, the scraper needs to switch into it"
                )
        except WebDriverException:
            pass
        return TimeoutException(detail)

    # -- browser tab plumbing -----------------------------------------------

    def _click_into_new_tab(self, element, watch_current_tab: bool = False) -> str | None:
        """Click a target="_blank" link and return the handle it opened, if any.

        With `watch_current_tab`, a click that navigates this tab instead of
        opening one ends the wait immediately and returns None. Without it, an
        in-place navigation would sit out the full NEW_TAB_TIMEOUT before the
        caller could notice — half a minute per button-rendered agency.
        """
        before = set(self.driver.window_handles)
        self.scroll_into_view(element)
        try:
            element.click()
        except StaleElementReferenceException:
            # The element is gone from the DOM — a JS click on it cannot work
            # either. The caller re-finds it or falls back to the URL.
            raise
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
            if watch_current_tab and self._left_the_network():
                return None
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

        # An agency opened in place navigated this very tab away from the
        # roster; there is nothing to wait for, so go straight back rather than
        # spending the timeout proving the list is gone.
        if self._left_the_network():
            self.navigate(settings.ridemetro_agencies_url)
            self.wait(60).until(EC.presence_of_element_located(network.SEL["go_to_agency"]))
            return

        # A tab switch alone doesn't guarantee the roster is still mounted (the
        # SPA re-renders on focus); make sure the controls are there before the
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

    def _select_account(self) -> None:
        """Resolve this run's login and confirm it can actually be used.

        The endpoint already checked this before creating the run; doing it again
        here covers a run started any other way, and means the browser is never
        launched for a login that cannot succeed. `require` raises with the
        `.env` keys to fix, which becomes the run's error.
        """
        self.set_step("selecting_account")
        requested = (run_manager.get_run(self.run_id) or {}).get("account")
        self.account = accounts.require(requested)
        # Key and label only: the run state goes to the console. The address is
        # logged instead, where it helps and is not on screen.
        run_manager.update_run(
            self.run_id,
            account=self.account.key,
            account_label=self.account.label,
        )
        logger.info(
            "[run %s] signing in as %s (%s)",
            self.run_id, self.account.label, accounts.mask(self.account.username),
        )

    def flush_partial(self) -> int:
        """RideMetro's rows. The sheet is named for the account the run used, as
        the completed path names it — two accounts sweeping the same day would
        otherwise land on one filename."""
        label = self.account.label if getattr(self, "account", None) else self.run_id
        return partials.flush_records(
            self,
            self._records,
            save_bids=export.save_bids,
            write_sheet=export.generate_excel_from_records,
            sheet_name=f"RideMetro_Bids ({label}) [{self.run_id}]",
        )

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        self._save_run_row()  # initial run row (best-effort)
        try:
            self._select_account()
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
        except StopRequested:
            # The user pressed Stop. run_manager has already locked the run to
            # "stopped" and is suppressing later status/error writes, so there
            # is nothing to record — but this must not fall through to the
            # handler below, which would log a traceback under "failed" and try
            # to screenshot a browser that stopping has already closed. A run
            # the user ended is not a run that broke.
            #
            # The rows gathered so far are saved and packaged here, because
            # everything that would have done it sits after the loop this stop
            # just unwound out of. See BaseScraper.deliver_partial.
            self.deliver_partial()
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
