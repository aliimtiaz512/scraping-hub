"""Selenium automation for PHLContracts — the City of Philadelphia's bid portal.

PHLContracts runs on Periscope Holdings' BSO platform. Two eras of markup sit
side by side, and the selectors below are split the same way:

* **The shell is JSF/PrimeFaces.** The home page's Sign In opens a PrimeFaces
  overlay panel (`PF('loginOP').show()`) whose fields carry JSF's colon-joined
  ids — `homeLoginForm:loginId`, `homeLoginForm:password`. A colon is a CSS
  pseudo-selector, so those are found by id through Selenium's `By.ID` (which
  does not parse them as CSS) and never by a hand-escaped selector.
* **Everything behind the login is 2000s table layout.** No ids on rows, no data
  attributes: the bid list is `table#resultsTable` and its cells are
  `td.tableText-01`, the detail page's header is a run of `td.t-head-01`
  label cells each followed by its `td.tableText-01` value, and attachments are
  anchors whose href is `javascript:editFile('391619')`.

Flow, per run:

    login (overlay, not a page)
    Bids tab            -> the seller dashboard's Bids view
    Open Bids section   -> the table under that heading, not the first on the page
    View More           -> the full Open Bids list rather than the dashboard's five
    for each bid row:   -> summary fields + the detail link
        open the detail page
        read every Header Information label/value pair
        read the line items, and write them as bid_items_details.txt
        download every File Attachment into the bid's own folder
    summary sheet -> database (upsert by bid number) -> one ZIP

**Nothing leaves here that a client cannot open.** The header table used to
travel beside each bid as JSON; it now reaches the reader as spreadsheet columns
(fiscal year, procurement type, pre-bid conference, and one cell holding every
other published label), and the line items go into the bid's folder as plain
text. The attachments are untouched — whole files, as the city published them.

**The attachments are the fiddly part.** Their anchors have no href to fetch:
`javascript:editFile('391619')` calls into the page, which streams the file
through the servlet. There is no URL to construct that is not a guess, so each
anchor is clicked and the browser's download is waited for — the same approach
the MyFlorida flow uses on the same kind of link. A file that never lands is
recorded against the bid by name rather than silently missing.
"""

from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select

from app.config import settings
from app.core import run_manager
from app.core.base_scraper import BaseScraper, StopRequested
from app.core.exports import archive_run
from app.scrapers.philadelphia import details, export, search, storage
from app.services.notifier import notify_scrape_completion

logger = logging.getLogger(__name__)

BASE_URL = "https://www.phlcontracts.phila.gov"
LOGIN_URL = f"{BASE_URL}/bso/view/login/login.xhtml"
#: Where "View More…" under Open Bids goes. Held as a URL as well as a link
#: because it is the whole list, and a run that cannot find the link can still
#: ask for the page it points at.
OPEN_BIDS_URL = f"{BASE_URL}/bso/bid/bidList.sda?status=Open&category=all"

# -- sign in ------------------------------------------------------------------
SIGN_IN_BUTTON = "home-sign-in-btn"          # opens the overlay; does not navigate
LOGIN_ID_INPUT = "homeLoginForm:loginId"     # JSF ids: found By.ID, never by CSS
LOGIN_PASSWORD_INPUT = "homeLoginForm:password"
LOGIN_SUBMIT = "homeLoginForm:sign-in-dialog-btn"
LOGIN_ERROR = "homeLoginForm:loginErrorMessage"

# The overlay renders before it is usable; these are how long we give each half.
OVERLAY_TIMEOUT = 30
LOGIN_TIMEOUT = 60

# -- the seller dashboard -----------------------------------------------------
#: The nav tab. Its href is `javascript:selectTab('B')`, so it is matched on that
#: rather than on the label, which carries a live count: "Bids(8768)".
BIDS_TAB_XPATH = "//a[contains(@href, \"selectTab('B')\")]"
#: The heading cell above the Open Bids table. The dashboard renders several
#: `table#resultsTable`s — Request for Revision, Bids / Bid Amendments, Open
#: Bids, Closed Bids — so the section is found by its heading and the table is
#: taken from *there*, never as "the first results table on the page".
OPEN_BIDS_HEADING_XPATH = (
    "//td[contains(@class,'sectionheader-02')]"
    "[contains(normalize-space(.), 'Open Bids')]"
)
OPEN_BIDS_TABLE_XPATH = f"{OPEN_BIDS_HEADING_XPATH}/following::table[@id='resultsTable'][1]"
VIEW_MORE_XPATH = "//a[contains(@href, 'bidList.sda') and contains(., 'View More')]"

#: A results row's bid link. The href is the row's identity as well as its
#: destination: `/bso/seller/bidAck.sda?status=Open&destination=detail&bidId=B2727750`.
BID_LINK_CSS = "a[href*='bidId=']"
RESULTS_TABLE_ID = "resultsTable"

# -- the detail page ----------------------------------------------------------
HEADER_LABEL_CSS = "td.t-head-01"
#: An attachment anchor, and — with the file id filled in — one specific
#: attachment. The id inside `editFile('391619')` is the anchor's identity,
#: which is how a file is found again after a download redraws the page.
ATTACHMENT_LINK_XPATH = "//a[contains(@href, 'editFile')]"
ATTACHMENT_BY_ID_XPATH = "//a[contains(@href, \"editFile('{file_id}')\")]"

SETTLE_SECONDS = 1.5
#: How long to wait for the File Attachments row to appear on a detail page, and
#: how many times to re-read it before accepting the count. Both are about a
#: list that is still being rendered, not about a bid with no attachments.
ATTACHMENT_TIMEOUT = 20
ATTACHMENT_SETTLE_READS = 3
#: Pagination guard on the Open Bids list. A backstop against a pager that never
#: terminates, not a cap on bids.
MAX_PAGES = 100
#: How long a page turn has to redraw the table before the walk gives up on it.
PAGE_TURN_TIMEOUT = 45
#: How long an Advanced Search has to come back with rows. Longer than a page
#: turn: a broad search over the whole catalogue is real work for the portal.
SEARCH_RESULTS_TIMEOUT = 90
#: Rows mirrored into the live run state for the console's table. Not a
#: processing limit — every record is stored, exported and packaged.
LIVE_PREVIEW_CEILING = 1000


class PhiladelphiaLoginError(Exception):
    """Sign-in did not complete. Carries a message worth showing an operator."""


class PhiladelphiaScraper(BaseScraper):
    """One run: sign in, read every open bid, and package what it found."""

    def __init__(self, run_id: str):
        super().__init__(run_id)
        self._records: list[dict[str, Any]] = []
        self._documents_downloaded = 0
        self._documents_failed = 0
        # Set by the dashboard's Advanced Search panel and carried on the run,
        # the same way `live_preview` is. Empty means the whole Open Bids list,
        # which is what a run did before this existed and still does by default.
        self.filters = search.clean_filters(
            (run_manager.get_run(run_id) or {}).get("filters")
        )

    # -- sign in --------------------------------------------------------------

    def login(self) -> None:
        """Open the overlay, fill it, and confirm we are actually inside.

        The Sign In button does not navigate — it calls `PF('loginOP').show()`
        and reveals a panel that is already in the DOM. So "the login page went
        away" proves nothing here, and the check at the end is for the seller
        dashboard's own Bids tab: the one thing that only exists once the
        session is real.
        """
        self.set_step("logging_in")
        if not settings.phila_email or not settings.phila_password:
            raise PhiladelphiaLoginError(
                "PHILA_EMAIL / PHILA_PASSWORD are not set in server/.env — "
                "PHLContracts needs a vendor account to list bids."
            )

        self.navigate(LOGIN_URL)
        self._open_login_overlay()

        user = self.wait(OVERLAY_TIMEOUT).until(
            EC.element_to_be_clickable((By.ID, LOGIN_ID_INPUT))
        )
        user.clear()
        user.send_keys(settings.phila_email)
        password = self.driver.find_element(By.ID, LOGIN_PASSWORD_INPUT)
        password.clear()
        password.send_keys(settings.phila_password)

        # The submit button is a PrimeFaces command button wired to
        # `submitLogin()`; a JS click reaches that handler whatever is overlaying
        # the panel.
        self.driver.execute_script(
            "arguments[0].click();", self.driver.find_element(By.ID, LOGIN_SUBMIT)
        )

        try:
            self.wait(LOGIN_TIMEOUT).until(lambda d: self._signed_in())
        except TimeoutException as exc:
            self.screenshot("login_failed")
            raise PhiladelphiaLoginError(
                f"PHLContracts sign-in did not complete within {LOGIN_TIMEOUT}s"
                + (f" — the portal said: {self._login_error()}" if self._login_error()
                   else ". Check PHILA_EMAIL / PHILA_PASSWORD in server/.env.")
            ) from exc
        logger.info("[run %s] signed in to PHLContracts", self.run_id)

    def _open_login_overlay(self) -> None:
        """Click Sign In, or show the overlay directly if the button is not there.

        Both routes end in the same panel. The direct call is the fallback for a
        landing page that renders the button differently (the portal serves a
        couple of variants of its home page) — the overlay is the same widget on
        all of them.
        """
        try:
            button = self.wait(OVERLAY_TIMEOUT).until(
                EC.element_to_be_clickable((By.ID, SIGN_IN_BUTTON))
            )
            self.driver.execute_script("arguments[0].click();", button)
        except (TimeoutException, WebDriverException):
            logger.info("[run %s] no Sign In button — showing the overlay directly", self.run_id)
            try:
                self.driver.execute_script("PF('loginOP').show();")
            except WebDriverException:
                pass  # the fields may already be on the page; the wait below decides
        time.sleep(SETTLE_SECONDS)

    def _signed_in(self) -> bool:
        """True once the seller dashboard is on screen."""
        try:
            return bool(self.driver.find_elements(By.XPATH, BIDS_TAB_XPATH))
        except WebDriverException:
            return False

    def _login_error(self) -> str:
        """The portal's own message from the login panel, or ""."""
        try:
            return (self.driver.find_element(By.ID, LOGIN_ERROR).text or "").strip()
        except (WebDriverException, TimeoutException):
            return ""

    # -- the Open Bids list ---------------------------------------------------

    def open_bids_list(self) -> None:
        """Reach the full Open Bids list: Bids tab, then View More.

        The dashboard's Open Bids table is a five-row preview with "View More…"
        under it. Following that link is the difference between five bids and
        every open one, so a run that cannot find the link goes to the URL the
        link points at rather than settling for the preview.
        """
        self.set_step("opening_bids")
        try:
            tab = self.wait().until(EC.element_to_be_clickable((By.XPATH, BIDS_TAB_XPATH)))
            self.driver.execute_script("arguments[0].click();", tab)
            time.sleep(SETTLE_SECONDS)
        except (TimeoutException, WebDriverException):
            logger.info("[run %s] Bids tab not clickable — going straight to the list",
                        self.run_id)

        self.set_step("expanding_open_bids")
        links = self.driver.find_elements(By.XPATH, VIEW_MORE_XPATH)
        if links:
            href = links[0].get_attribute("href") or OPEN_BIDS_URL
            logger.info("[run %s] following View More to the full Open Bids list", self.run_id)
            self.navigate(urljoin(BASE_URL, href))
        else:
            logger.info(
                "[run %s] no View More link on the dashboard — requesting the Open "
                "Bids list directly", self.run_id,
            )
            self.navigate(OPEN_BIDS_URL)
        self._await_results_table()

    # -- Advanced Search ------------------------------------------------------

    def advanced_search(self, filters: dict[str, Any]) -> None:
        """Run the portal's Advanced Search instead of taking the whole open list.

        Advanced -> Document Type: Bid Solicitations -> fill -> Search. What
        comes back is the same results table the Open Bids list renders, so
        everything downstream is untouched: the same extractor reads it, the
        same pager walks it, and the same detail/attachment/export pipeline
        runs over what it finds.
        """
        self.set_step("opening_advanced_search")
        logger.info("[ADVANCED SEARCH]: %s", search.describe(filters))
        self._open_advanced_search()
        self._choose_bid_solicitations()
        self._fill_search_form(filters)
        self._submit_search()

    def _open_advanced_search(self) -> None:
        """Follow the top bar's Advanced link, or ask for the page it points at.

        The link is `javascript:gotoBsoURL('view/search/supplier/advancedSearch.xhtml')`
        — a script call rather than an href to follow — so it is clicked. A
        portal that has moved or renamed it still has the page.
        """
        try:
            link = self.wait(20).until(
                EC.element_to_be_clickable((By.ID, search.ADVANCED_LINK_ID))
            )
            self.driver.execute_script("arguments[0].click();", link)
            time.sleep(SETTLE_SECONDS)
            logger.info(" ├── opened Advanced Search from the top bar")
            return
        except (TimeoutException, WebDriverException):
            logger.info(
                "[run %s] no Advanced link in the top bar — requesting the "
                "advanced search page directly", self.run_id,
            )
        self.navigate(BASE_URL + search.ADVANCED_SEARCH_PATH)
        time.sleep(SETTLE_SECONDS)

    def _choose_bid_solicitations(self) -> None:
        """Set Document Type to Bid Solicitations and wait for the form.

        The page's own script returns early for this value rather than
        navigating: PrimeFaces re-renders the criteria panel over AJAX. So the
        thing to wait for is the bid form appearing, not a page load — and if
        the dropdown cannot be driven at all, the page it would have produced
        can be asked for by URL.
        """
        if self._search_form_present():
            logger.info(" ├── the bid criteria form is already on the page")
            return
        try:
            select = Select(self.wait(20).until(
                EC.presence_of_element_located((By.ID, search.DOCUMENT_TYPE_SELECT_ID))
            ))
            select.select_by_value(search.BID_SOLICITATIONS)
            logger.info(" ├── Document Type: Bid Solicitations")
        except (TimeoutException, WebDriverException):
            logger.info(
                "[run %s] the Document Type dropdown could not be set — going "
                "straight to the bid search page", self.run_id,
            )
            self.navigate(BASE_URL + search.BID_SEARCH_PATH)

        try:
            self.wait(30).until(
                EC.presence_of_element_located((By.ID, search.SEARCH_FORM_ID))
            )
        except TimeoutException:
            # One more try by URL before giving up: the selection is the
            # documented route, but the page behind it is what matters.
            logger.warning("[run %s] the criteria form did not render — asking for "
                           "the bid search page by URL", self.run_id)
            self.navigate(BASE_URL + search.BID_SEARCH_PATH)
            self.wait(30).until(
                EC.presence_of_element_located((By.ID, search.SEARCH_FORM_ID))
            )
        time.sleep(SETTLE_SECONDS)

    def _search_form_present(self) -> bool:
        try:
            return bool(self.driver.find_elements(By.ID, search.SEARCH_FORM_ID))
        except WebDriverException:
            return False

    def _fill_search_form(self, filters: dict[str, Any]) -> None:
        """Put every criterion on the form, parents before their dependants.

        A criterion that will not go in is reported rather than dropped: a run
        that silently searched on less than it was asked for returns the wrong
        bids and looks like it worked.
        """
        applied, missed = [], []
        for key in search.ORDERED_FIELDS:
            if key not in filters:
                continue
            if self._fill_one_criterion(key, str(filters[key])):
                applied.append(f"{search.LABELS[key]}: {filters[key]}")
            else:
                missed.append(f"{search.LABELS[key]}: {filters[key]}")

        if filters.get("match_any"):
            if self._set_match_any():
                applied.append("Match: any criterion")
            else:
                missed.append("Match: any criterion")

        for line in applied:
            logger.info(" ├── %s", line)
        if missed:
            logger.warning("[run %s] %d criterion/criteria could not be applied: %s",
                           self.run_id, len(missed), "; ".join(missed))
            run_manager.add_warning(
                self.run_id,
                "the portal's search form would not take these criteria, so the "
                f"results are broader than asked for — {'; '.join(missed)}",
            )

    def _fill_one_criterion(self, key: str, value: str) -> bool:
        parent = search.DEPENDS_ON.get(key)
        if parent:
            # Organization fills Department over AJAX, NIGP Class fills its
            # Item. Both start `disabled` with a single empty option, so the
            # wait is for the control to have something in it to choose.
            if not self._await_dependent_select(search.SELECT_FIELDS[key]):
                logger.warning("[run %s] %s never filled in after %s was set",
                               self.run_id, search.LABELS[key], search.LABELS[parent])
                return False
        if key in search.TEXT_FIELDS:
            return self._type_into(search.TEXT_FIELDS[key], value)
        return self._select_option(search.SELECT_FIELDS[key], value)

    def _await_dependent_select(self, element_id: str, timeout: int = 20) -> bool:
        """Wait for a select the page fills in for itself to be usable."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.raise_if_stopped()
            try:
                element = self.driver.find_element(By.ID, element_id)
                if element.is_enabled() and len(Select(element).options) > 1:
                    return True
            except WebDriverException:
                pass
            time.sleep(0.5)
        return False

    def _type_into(self, element_id: str, value: str) -> bool:
        try:
            field = self.driver.find_element(By.ID, element_id)
            self.scroll_into_view(field)
            field.clear()
            field.send_keys(value)
            return True
        except WebDriverException as exc:
            logger.info("[run %s] could not type into %s (%s)",
                        self.run_id, element_id, exc.__class__.__name__)
            return False

    def _select_option(self, element_id: str, value: str) -> bool:
        """Choose an option by its value, or by what it says on screen.

        The caller sends what a person would write — "Micro Purchase", or the
        buyer's name — and the portal's own codes are `MI` and `MP_PROD`.
        Matching the visible text as well as the value is what lets the form on
        the dashboard stay in the words of the person filling it in.
        """
        try:
            element = self.driver.find_element(By.ID, element_id)
        except WebDriverException:
            return False
        if not element.is_enabled():
            return False

        select = Select(element)
        wanted = value.strip().casefold()
        options = select.options

        exact_value = next(
            (o for o in options if (o.get_attribute("value") or "").casefold() == wanted), None)
        exact_text = next(
            (o for o in options if (o.text or "").strip().casefold() == wanted), None)
        # Only among options that have a value: "Select Buyer..." is the empty
        # placeholder and matches far too readily on a partial.
        partial = next(
            (o for o in options
             if (o.get_attribute("value") or "").strip()
             and wanted in (o.text or "").casefold()), None)

        chosen = exact_value or exact_text or partial
        if chosen is None:
            logger.info("[run %s] %s has no option matching %r",
                        self.run_id, element_id, value)
            return False
        try:
            self.scroll_into_view(element)
            select.select_by_value(chosen.get_attribute("value") or "")
            time.sleep(0.5)   # a change here can start an AJAX round trip
            return True
        except WebDriverException:
            return False

    def _set_match_any(self) -> bool:
        """Flip "Match Criteria" from All to Any.

        The visible switch is PrimeFaces chrome over a checkbox the theme hides
        in `.ui-helper-hidden-accessible`, so the checkbox cannot be clicked —
        the widget is. The widget is clicked first so PrimeFaces updates its own
        state along with the input, and the *checkbox* is what is then checked,
        because that is the value the form actually submits. If the click did
        not take, the input is set directly and told it changed — the switch
        may then look wrong on screen, but the search that runs is the one that
        was asked for.
        """
        try:
            checkbox = self.driver.find_element(By.ID, search.MATCH_ANY_SWITCH_ID)
        except WebDriverException:
            logger.info("[run %s] no Match Criteria control on this form", self.run_id)
            return False
        if checkbox.is_selected():
            return True

        try:
            switch = self.driver.find_element(
                By.ID, search.MATCH_ANY_SWITCH_ID.removesuffix("_input"))
            self.scroll_into_view(switch)
            self.driver.execute_script("arguments[0].click();", switch)
            time.sleep(0.5)
        except WebDriverException:
            pass
        if checkbox.is_selected():
            return True

        try:
            self.driver.execute_script(
                "arguments[0].checked = true;"
                "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                checkbox,
            )
            time.sleep(0.5)
        except WebDriverException as exc:
            logger.info("[run %s] could not set Match Criteria (%s)",
                        self.run_id, exc.__class__.__name__)
            return False
        return checkbox.is_selected()

    def _submit_search(self) -> None:
        """Click Search and wait for the results panel to answer.

        The button runs a PrimeFaces AJAX update of the results container, so
        there is no navigation: what arrives is either rows or an empty panel,
        and both are answers. An empty one is reported as "no bids matched"
        rather than as a page that failed to load.
        """
        self.set_step("running_advanced_search")
        button = self.wait(20).until(
            EC.element_to_be_clickable((By.ID, search.SEARCH_BUTTON_ID))
        )
        self.scroll_into_view(button)
        self.driver.execute_script("arguments[0].click();", button)
        logger.info(" └── [SEARCH SUBMITTED]: waiting for results…")

        try:
            self.wait(SEARCH_RESULTS_TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, BID_LINK_CSS))
            )
        except TimeoutException:
            # No rows may be the true answer. `collect_rows` reports zero and
            # logs what the page held, which distinguishes the two.
            logger.info("[run %s] the search returned no bid rows", self.run_id)
        time.sleep(SETTLE_SECONDS)

    def _await_results_table(self) -> None:
        """Wait for bid rows, not for a table id.

        `#resultsTable` is the *dashboard's* id. The full Open Bids list
        (`bidList.sda`) is a different page and a live run proved it does not
        carry it — the wait timed out, the extractor found nothing, and a
        listing full of bids was reported as empty. What both pages do have is
        rows whose Bid # cell links to `…bidId=…`, so that is what is waited on.
        """
        try:
            self.wait().until(
                EC.presence_of_element_located((By.CSS_SELECTOR, BID_LINK_CSS))
            )
        except TimeoutException:
            # Not fatal on its own: a list with genuinely no open bids has no
            # such links either. `collect_rows` reports zero and the diagnostic
            # below says which of the two it was.
            logger.info("[run %s] no bid links on the Open Bids page", self.run_id)
        time.sleep(SETTLE_SECONDS)

    # Reads every Open Bids row on the page in one pass. Three strategies, in
    # order, because the two pages that carry this list are not the same page:
    #
    #   1. the table under an "Open Bids" heading — the dashboard, which renders
    #      four `table#resultsTable`s (Request for Revision, Bids / Bid
    #      Amendments, Open Bids, Closed Bids) and where taking the first would
    #      scrape the wrong section;
    #   2. the table whose header row says "Bid #" — the full `bidList.sda`
    #      list, which a live run showed does *not* use `id="resultsTable"`;
    #   3. whichever table holds the most `bidId=` links — the backstop for a
    #      page that labels its headers differently again.
    #
    # Keying on the id alone is what made a listing full of bids read as empty.
    # Every strategy ends at a <table>, and the columns are then located by
    # header name and read positionally, so a column inserted before Description
    # does not silently shift the values.
    _JS_ROWS = """
    const clean = (el) => ((el ? el.textContent : '') || '').replace(/\\s+/g, ' ').trim();

    const tableOf = (node) => (node ? node.closest('table') : null);
    const linkCount = (t) => t.querySelectorAll("a[href*='bidId=']").length;

    // These three are the difference between reading the bid table and reading
    // the layout table that contains it. This markup nests tables for layout,
    // and `querySelectorAll` does not stop at the nesting:
    //
    //   * document order returns a wrapper BEFORE the table it wraps, so every
    //     strategy below matches the wrapper first — it carries the same bid
    //     links, by descent;
    //   * a wrapper's `tr` list includes the layout row holding the whole bid
    //     table, whose `td` list is every cell in it flattened into one row —
    //     a phantom bid with the first bid's number and the wrong everything;
    //   * a wrapper's `th` list includes headers from sibling tables (a search
    //     panel, a totals strip), which shifts every column index by however
    //     many it picked up.
    //
    // So: descend to the innermost table that still holds the links, and read
    // only the rows and cells that belong to it rather than to something nested
    // inside it.
    const innermost = (t) => {
      let current = t;
      while (current) {
        const wanted = linkCount(current);
        const inner = [...current.querySelectorAll('table')]
          .filter((c) => linkCount(c) === wanted);
        if (!inner.length) return current;
        current = inner[inner.length - 1];   // the deepest one holding them all
      }
      return t;
    };
    const ownRows = (t) => [...t.querySelectorAll('tr')].filter((tr) => tableOf(tr) === t);
    const ownCells = (tr) =>
      [...tr.querySelectorAll('td, th')].filter((c) => c.closest('tr') === tr);

    // (1) the section heading, then the first results table after it.
    let table = null;
    let strategy = '';
    const heading = [...document.querySelectorAll('td, th')].find(
      (cell) => (cell.className || '').toLowerCase().includes('sectionheader')
                && clean(cell).toLowerCase().includes('open bids'));
    if (heading) {
      const scope = tableOf(heading);
      const outer = scope ? (scope.parentElement ? tableOf(scope.parentElement) || scope : scope)
                          : document;
      const nested = [...(outer || document).querySelectorAll('table')]
        .filter((t) => linkCount(t) > 0);
      if (nested.length) { table = nested[0]; strategy = 'open-bids heading'; }
    }

    // (2) the table whose header row names the Bid # column.
    if (!table) {
      for (const candidate of document.querySelectorAll('table')) {
        const headers = [...candidate.querySelectorAll('th')]
          .map((th) => clean(th).toLowerCase());
        if (headers.some((h) => h.replace(/\\s+/g, '').includes('bid#'))
            && linkCount(candidate) > 0) {
          table = candidate; strategy = 'bid # header'; break;
        }
      }
    }

    // (3) whichever table carries the most bid links.
    if (!table) {
      let best = 0;
      for (const candidate of document.querySelectorAll('table')) {
        const count = linkCount(candidate);
        if (count > best) { best = count; table = candidate; strategy = 'most bid links'; }
      }
    }

    if (!table) {
      // Nothing to read. Report what IS on the page so the next run's log says
      // which page it was looking at rather than only that it found nothing.
      return {
        found: false, rows: [], strategy: '',
        diagnostic: {
          url: location.href,
          title: document.title,
          tables: document.querySelectorAll('table').length,
          table_ids: [...document.querySelectorAll('table[id]')]
            .map((t) => t.id).slice(0, 12),
          bid_links: document.querySelectorAll("a[href*='bidId=']").length,
          frames: document.querySelectorAll('frame, iframe').length,
          text: (document.body ? document.body.innerText : '')
            .replace(/\\s+/g, ' ').trim().slice(0, 400),
        },
      };
    }

    // Every strategy above can land on a layout wrapper. This is where that is
    // undone, once, for all three.
    table = innermost(table);

    // The header row is this table's own — the first of its rows that is made
    // of header cells. Taking every `th` in the table would take a nested
    // table's headers too, and each stray one shifts the columns by a place.
    let headerCells = [];
    for (const tr of ownRows(table)) {
      const cells = ownCells(tr).filter((c) => c.tagName === 'TH');
      if (cells.length >= 3) { headerCells = cells; break; }
    }
    const headers = headerCells.map((th) => clean(th).toLowerCase());
    const columnFor = (...names) => {
      for (const name of names) {
        const index = headers.findIndex((h) => h.includes(name));
        if (index >= 0) return index;
      }
      return -1;
    };
    const index = {
      bid: columnFor('bid #', 'bid#'),
      organization: columnFor('organization'),
      alternate: columnFor('alternate'),
      buyer: columnFor('buyer'),
      description: columnFor('description'),
      opening: columnFor('opening date', 'bid opening'),
    };
    // A list page that renders no <th> at all still has to be readable: fall
    // back to the column order the portal uses everywhere it shows this list.
    const fallback = {bid: 0, organization: 1, alternate: 2, buyer: 3, description: 4, opening: 5};
    for (const key of Object.keys(index)) {
      if (index[key] < 0) index[key] = fallback[key];
    }
    const at = (cells, i) => (i >= 0 && i < cells.length ? clean(cells[i]) : '');

    const rows = [];
    for (const tr of ownRows(table)) {
      // A row that contains a table is holding a layout, not a bid. Its cell
      // list would be every cell inside it flattened into one row — which is
      // how one bid's number ended up against another bid's description.
      if (tr.querySelector('table')) continue;
      const cells = ownCells(tr).filter((c) => c.tagName === 'TD');
      if (cells.length < 4) continue;
      const link = tr.querySelector("a[href*='bidId=']");
      if (!link) continue;                     // header, spacer, or the View More row
      rows.push({
        bid_number: clean(link) || at(cells, index.bid),
        detail_url: link.href,
        organization: at(cells, index.organization),
        alternate_id: at(cells, index.alternate),
        buyer: at(cells, index.buyer),
        description: at(cells, index.description),
        bid_opening_date: at(cells, index.opening),
      });
    }
    return {found: true, rows: rows, strategy: strategy, diagnostic: null};
    """

    def collect_rows(self) -> list[dict[str, Any]]:
        """Every Open Bids row on screen, in portal order.

        Searches the whole document, then every frame: BSO renders some of its
        list pages inside a frameset, and a script that only ever reads the top
        document finds nothing on those without being able to say why.
        """
        result = self._rows_in_current_document()
        if result.get("found"):
            logger.info(
                "[run %s] read the bid list via the %s",
                self.run_id, result.get("strategy") or "results table",
            )
            return [row for row in result.get("rows", []) if row.get("bid_number")]

        framed = self._rows_in_frames()
        if framed is not None:
            return framed

        # Nothing anywhere. Say what the page actually was — a zero that cannot
        # tell "no open bids today" from "the list is somewhere this did not
        # look" is the reason a listing full of bids read as empty.
        self._report_empty_page(result.get("diagnostic") or {})
        return []

    def _rows_in_current_document(self) -> dict[str, Any]:
        try:
            return self.driver.execute_script(self._JS_ROWS) or {}
        except WebDriverException as exc:
            logger.warning("[run %s] could not read the bid table (%s)",
                           self.run_id, exc.__class__.__name__)
            return {}

    def _rows_in_frames(self) -> list[dict[str, Any]] | None:
        """The rows from whichever frame holds the list, or None if none does."""
        try:
            frames = self.driver.find_elements(By.CSS_SELECTOR, "frame, iframe")
        except WebDriverException:
            return None
        if not frames:
            return None

        logger.info("[run %s] no bid list in the page itself — checking %d frame(s)",
                    self.run_id, len(frames))
        for index in range(len(frames)):
            try:
                self.driver.switch_to.default_content()
                self.driver.switch_to.frame(index)
                result = self._rows_in_current_document()
            except WebDriverException:
                continue
            if result.get("found"):
                rows = [row for row in result.get("rows", []) if row.get("bid_number")]
                logger.info(
                    "[run %s] the bid list is in frame %d (%s) — %d row(s)",
                    self.run_id, index, result.get("strategy"), len(rows),
                )
                # Deliberately left switched in: the detail links are followed by
                # URL, and the next page's read wants this same frame.
                return rows
        try:
            self.driver.switch_to.default_content()
        except WebDriverException:
            pass
        return None

    def _report_empty_page(self, diagnostic: dict[str, Any]) -> None:
        """Log what the page was, so the next attempt starts from evidence."""
        if not diagnostic:
            logger.info("[run %s] no bid rows found and the page could not be read",
                        self.run_id)
            return
        logger.warning(
            "[run %s] no bid rows found — url=%s | title=%r | %s table(s) "
            "(ids: %s) | %s bidId links | %s frame(s) | page text: %.200s",
            self.run_id, diagnostic.get("url"), diagnostic.get("title"),
            diagnostic.get("tables"), ", ".join(diagnostic.get("table_ids") or []) or "none",
            diagnostic.get("bid_links"), diagnostic.get("frames"),
            diagnostic.get("text") or "",
        )
        self.screenshot("no_bid_rows")

    def collect_all_pages(self) -> list[dict[str, Any]]:
        """Walk the Open Bids list, page by page, accumulating rows.

        The list shows 25 bids at a time, so a run that reads one page reads 25
        of however many the city has open. The accumulator lives here rather
        than inside the page loop, and rows are de-duplicated on the bid number —
        the one field that identifies a bid — so a list that shifts under the
        walk cannot turn a repeat into a loss.

        The de-duplication is row-by-row, not page-by-page: the city's list can
        print one bid on several lines (one per commodity it is classified
        under), so a repeat inside a single page is as ordinary as a repeat
        across two.

        The walk ends on whichever comes first: a pager with no page after this
        one, a total that says every record has been read, or the page cap.
        """
        self.set_step("collecting_bids")
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        seen_urls: set[str] = set()
        total: int | None = None
        pages_read = 0

        logger.info("[PAGINATION INITIALIZED]: Scraping Open Bids...")

        for page in range(1, MAX_PAGES + 1):
            page_rows = self.collect_rows()
            if page == 1 and not page_rows:
                # The full list page gave nothing. The dashboard's own Open Bids
                # table is a different page that demonstrably renders — so fall
                # back to it rather than reporting a portal full of bids as
                # empty. It is a five-row preview, so this is a degraded run and
                # says so; it is not a substitute for the full list.
                page_rows = self._dashboard_preview_rows()

            new = []
            for row in page_rows:
                if row["bid_number"] in seen:
                    continue
                seen.add(row["bid_number"])
                new.append(row)
                self._log_row(len(rows) + len(new), row)
            rows.extend(new)
            pages_read = page

            duplicates = len(page_rows) - len(new)
            logger.info(
                " ├── [PAGE %d]: %d record%s extracted successfully.%s",
                page, len(page_rows), "" if len(page_rows) == 1 else "s",
                f" ({duplicates} already seen)" if duplicates else "",
            )
            run_manager.update_run(self.run_id, bids_found=len(rows), page=page)

            pager = self._pager_state()
            if pager.get("total"):
                total = pager["total"]

            if total is not None and len(seen) >= total:
                # Every record the portal counted is in hand. Believe the total
                # over the pager: a "3" link that redraws the same 25 rows would
                # otherwise walk forever.
                break

            if not self._turn_page(pager, seen_urls, page):
                break
        else:
            logger.error("[run %s] stopped at the page cap (%d)", self.run_id, MAX_PAGES)

        logger.info(
            " └── [PAGINATION COMPLETE]: Total %d record%s accumulated across %d page%s.",
            len(rows), "" if len(rows) == 1 else "s",
            pages_read, "" if pages_read == 1 else "s",
        )
        if total is not None and len(rows) < total:
            # Said plainly rather than left to be inferred from two numbers in a
            # log: a short run is either duplicates in the city's own list or a
            # page turn that did not take, and both are worth knowing about.
            logger.warning(
                "[run %s] the portal reported %d record(s) and the walk kept %d — "
                "the difference is repeated bid numbers unless a page turn failed "
                "above", self.run_id, total, len(rows),
            )
        return rows

    @staticmethod
    def _log_row(number: int, row: dict[str, Any]) -> None:
        """One parsed row, field by field.

        Verbose on purpose: a column that has shifted is invisible in a count and
        obvious the moment the values are named next to their fields.
        """
        logger.info(
            "[PARSING ROW %d]:\n"
            " ├── Bid Number: %s\n"
            " ├── Organization: %s\n"
            " ├── Buyer: %s\n"
            " ├── Description: %s\n"
            " └── Opening Date: %s",
            number,
            row.get("bid_number") or "—",
            row.get("organization") or "—",
            row.get("buyer") or "—",
            row.get("description") or "—",
            row.get("bid_opening_date") or "—",
        )

    # -- pagination -----------------------------------------------------------

    # The pager, read in one pass. BSO renders it as
    #
    #     <td class="inputs-01" aria-live="polite">1-25 of 41</td>
    #     <nav aria-label="Pagination">
    #       <span aria-current="page">1</span>
    #       <a href="javascript:viewPage(2)" class="link-01">2</a>
    #     </nav>
    #
    # There is no Next button — only a numbered anchor per other page — and the
    # hrefs are script calls, not URLs. So the page after this one is the anchor
    # labelled `current + 1`, and turning to it means clicking rather than
    # navigating. The counter above the nav is where the total comes from.
    _JS_PAGER = """
    const clean = (el) => ((el ? el.textContent : '') || '').replace(/\\s+/g, ' ').trim();

    let nav = document.querySelector("nav[aria-label='Pagination']");
    if (!nav) {
      nav = [...document.querySelectorAll('nav')].find(
        (n) => /pagination/i.test(n.getAttribute('aria-label') || ''));
    }
    // A pager with no <nav> at all: fall back to the element holding the page
    // links, found through a viewPage() anchor.
    if (!nav) {
      const link = document.querySelector("a[href*='viewPage(']");
      if (link) nav = link.parentElement;
    }

    // "1-25 of 41" — the counter is the only place the total appears.
    let counter = '';
    for (const cell of document.querySelectorAll('td, span, div')) {
      const text = clean(cell);
      if (/^\\d+\\s*[-–]\\s*\\d+\\s+of\\s+[\\d,]+$/i.test(text)) { counter = text; break; }
    }
    const parts = counter.match(/(\\d+)\\s*[-–]\\s*(\\d+)\\s+of\\s+([\\d,]+)/i);
    const num = (s) => parseInt(String(s).replace(/,/g, ''), 10);

    let current = null;
    if (nav) {
      const marker = nav.querySelector('[aria-current]');
      const parsed = num(clean(marker));
      if (!isNaN(parsed)) current = parsed;
    }
    // No aria-current to read: derive the page from the record range instead
    // (rows 26-41 of a 25-row page is page 2).
    if (current === null && parts) {
      const from = num(parts[1]), to = num(parts[2]);
      const size = to - from + 1;
      if (size > 0) current = Math.floor((from - 1) / size) + 1;
    }

    const labels = nav ? [...nav.querySelectorAll('a[href]')].map(clean) : [];
    const next = current !== null && labels.includes(String(current + 1))
      ? current + 1 : null;

    return {
      found: !!(nav || counter),
      counter: counter,
      from: parts ? num(parts[1]) : null,
      to: parts ? num(parts[2]) : null,
      total: parts ? num(parts[3]) : null,
      current: current,
      next: next,
      labels: labels,
    };
    """

    # What is on screen right now, as one string. Compared before and after a
    # page turn: the click and the redraw are not the same event, and the table
    # keeps its selectors across pages, so waiting for "a table with rows" would
    # be satisfied by the page we are trying to leave. The counter alone would
    # do it on a well-behaved pager; the first bid number and the row count are
    # there for a pager that renders the counter late.
    _JS_SIGNATURE = """
    const clean = (el) => ((el ? el.textContent : '') || '').replace(/\\s+/g, ' ').trim();
    let counter = '';
    for (const cell of document.querySelectorAll('td, span, div')) {
      const text = clean(cell);
      if (/^\\d+\\s*[-–]\\s*\\d+\\s+of\\s+[\\d,]+$/i.test(text)) { counter = text; break; }
    }
    const links = [...document.querySelectorAll("a[href*='bidId=']")];
    // No rows here means this is not the list — a frame we have been switched
    // out of, or a redraw caught mid-flight. Reported as "cannot say" rather
    // than as a fingerprint, so an empty document never reads as a page turn.
    if (!links.length) return '';
    return [counter, links.length, clean(links[0]), clean(links[links.length - 1])].join('|');
    """

    def _pager_state(self) -> dict[str, Any]:
        """The pager as numbers: which page, how many records, what comes next.

        `{}` when the page has no pager — a list that fits on one page, which is
        a complete read rather than a failure.
        """
        try:
            state = self.driver.execute_script(self._JS_PAGER) or {}
        except WebDriverException as exc:
            logger.info("[run %s] could not read the pager (%s)",
                        self.run_id, exc.__class__.__name__)
            return {}
        if not state.get("found"):
            return {}
        logger.debug("[run %s] pager: %s", self.run_id, state)
        return state

    def _list_signature(self) -> str:
        """A fingerprint of the rows on screen, or "" if they cannot be read.

        "" is "cannot say", never "no rows": the caller keeps waiting on it
        rather than treating it as a change.
        """
        try:
            return self.driver.execute_script(self._JS_SIGNATURE) or ""
        except WebDriverException:
            pass
        # The frame we were reading in is gone — which is what a page turn that
        # navigates the whole frameset looks like from inside one. Step back out
        # and read the top document; `collect_rows` re-finds the frame from
        # there if the list is still in one.
        try:
            self.driver.switch_to.default_content()
            return self.driver.execute_script(self._JS_SIGNATURE) or ""
        except WebDriverException:
            # Mid-navigation there is no document to read at all. Not an answer.
            return ""

    def _turn_page(
        self, pager: dict[str, Any], seen_urls: set[str], page: int
    ) -> bool:
        """Go to the page after this one. False when there isn't one.

        Two pagers to satisfy: PHLContracts' script-call anchors, which have to
        be clicked, and the plain `?page=` hrefs other BSO list pages use, which
        can be navigated to. The click is tried first because it is what this
        portal renders; the URL is the fallback that keeps the older pages
        working.
        """
        target = pager.get("next")
        if target:
            logger.info(" ├── [PAGINATING]: Navigating to Page %d...", target)
            if self._click_to_page(target):
                return True
            logger.warning(
                "[run %s] page %d did not redraw the list — stopping the walk here "
                "rather than re-reading page %d", self.run_id, target, page,
            )
            self.screenshot(f"page_{target}_no_redraw")
            return False

        next_url = self._next_page_url(seen_urls)
        if not next_url:
            return False
        logger.info(" ├── [PAGINATING]: Navigating to Page %d...", page + 1)
        seen_urls.add(next_url)
        self.navigate(next_url)
        self._await_results_table()
        return True

    def _click_to_page(self, target: int) -> bool:
        """Click the pager's link to `target` and wait for the table to redraw.

        Returns False if there was no clickable link, or if the list still looks
        exactly as it did — which is the honest answer for a page turn that did
        not take, and stops the walk from re-reading one page forever.
        """
        link = self._page_link(target)
        if link is None:
            return False

        before = self._list_signature()
        try:
            self.scroll_into_view(link)
            link.click()
        except WebDriverException:
            # An overlay or a re-render between finding the link and clicking it.
            # The scripted click lands anyway — and an `href` of
            # `javascript:viewPage(2)` runs the same either way.
            try:
                self.driver.execute_script("arguments[0].click();", link)
            except WebDriverException as exc:
                logger.warning("[run %s] could not click page %d (%s)",
                               self.run_id, target, exc.__class__.__name__)
                return False
        return self._await_page_change(before)

    def _page_link(self, target: int) -> Any | None:
        """The pager's link to `target`, if it is on the page and usable."""
        candidates = (
            # The script call names the page it goes to, so this is exact.
            f"//a[contains(@href, 'viewPage({target})')]",
            # A pager that numbers its links some other way: match the label,
            # inside the nav so a "2" elsewhere on the page cannot be picked up.
            "//nav[contains(translate(@aria-label, 'PAGINATION', 'pagination'), 'pagination')]"
            f"//a[normalize-space(.)='{target}']",
            # And the plain Next control, for the pages that render one.
            "//a[contains(translate(., 'NEXT', 'next'), 'next')]",
        )
        for xpath in candidates:
            try:
                links = self.driver.find_elements(By.XPATH, xpath)
            except WebDriverException:
                continue
            for link in links:
                try:
                    if link.is_displayed() and link.is_enabled():
                        return link
                except WebDriverException:
                    continue
        logger.info("[run %s] no link to page %d on this page", self.run_id, target)
        return None

    def _await_page_change(self, before: str, timeout: int = PAGE_TURN_TIMEOUT) -> bool:
        """Wait until the list is not the one we just left.

        `viewPage()` redraws the table in place, so there is no load to wait on
        and no new selector to appear — what changes is the content. Polling the
        fingerprint covers both shapes this portal uses: an in-place redraw and
        a full navigation.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.raise_if_stopped()  # a page turn is a fine place to cancel a run
            time.sleep(0.5)
            current = self._list_signature()
            if current and current != before:
                time.sleep(SETTLE_SECONDS)  # let the last of the rows settle
                return True
        logger.warning("[run %s] the list did not change within %ds of the page turn",
                       self.run_id, timeout)
        return False

    def _dashboard_preview_rows(self) -> list[dict[str, Any]]:
        """The Open Bids table on the seller dashboard, as a last resort.

        Returns [] when the dashboard has nothing either — which is then a real
        "no open bids" rather than a page this could not read.
        """
        logger.warning(
            "[run %s] the full Open Bids list yielded no rows — falling back to "
            "the dashboard's Open Bids table (a preview, not the whole list)",
            self.run_id,
        )
        try:
            self.driver.switch_to.default_content()
        except WebDriverException:
            pass
        self.navigate(BASE_URL + "/bso/view/portal/portalMain.xhtml")
        try:
            tab = self.wait(30).until(EC.element_to_be_clickable((By.XPATH, BIDS_TAB_XPATH)))
            self.driver.execute_script("arguments[0].click();", tab)
            time.sleep(SETTLE_SECONDS)
        except (TimeoutException, WebDriverException):
            logger.info("[run %s] could not reopen the Bids tab", self.run_id)

        rows = self.collect_rows()
        if rows:
            run_manager.add_warning(
                self.run_id,
                f"the full Open Bids list could not be read, so this run covers only "
                f"the {len(rows)} bid(s) the dashboard previews — see the log for what "
                f"that page contained",
            )
        return rows

    def _next_page_url(self, seen: set[str]) -> str | None:
        """The next page's URL, or None when there is no link-based pager.

        The fallback for a list page that paginates with real hrefs rather than
        the Open Bids list's `javascript:viewPage(n)` anchors — those are turned
        by `_click_to_page`, and a script call is not a URL, so they are skipped
        here. Only forward links are followed, matched on the word "Next" or on
        a `>`-style arrow, and a URL already visited ends the walk, so a pager
        that hands back the page it is on cannot loop.
        """
        candidates = (
            "//a[contains(translate(., 'NEXT', 'next'), 'next') and @href]",
            "//a[normalize-space(.)='>' and @href]",
        )
        for xpath in candidates:
            try:
                links = self.driver.find_elements(By.XPATH, xpath)
            except WebDriverException:
                continue
            for link in links:
                href = (link.get_attribute("href") or "").strip()
                if not href or href.lower().startswith("javascript:"):
                    continue
                absolute = urljoin(BASE_URL, href)
                if absolute in seen or absolute == (self.driver.current_url or ""):
                    continue
                return absolute
        return None

    # -- one bid --------------------------------------------------------------

    # Every Header Information label/value pair, in document order. The table is
    # `td.t-head-01` (label) followed by its value cell, repeated across the row
    # — so pairs are read by walking the label cells and taking the next cell
    # along, which survives the rows that carry two or three pairs each.
    _JS_HEADER = """
    const clean = (el) => ((el ? el.innerText : '') || '')
      .replace(/\\u00a0/g, ' ').replace(/[ \\t]+/g, ' ').trim();
    const out = {};
    for (const label of document.querySelectorAll('td.t-head-01')) {
      const name = clean(label).replace(/:\\s*$/, '');
      if (!name) continue;
      let value = label.nextElementSibling;
      // Skip empty spacer cells the layout inserts between pairs.
      while (value && !clean(value) && value.nextElementSibling
             && !(value.className || '').includes('t-head-01')) {
        value = value.nextElementSibling;
      }
      if (!value || (value.className || '').includes('t-head-01')) continue;
      const text = clean(value);
      if (text && !(name in out)) out[name] = text;
    }
    return out;
    """

    # The bid's line items. Two shapes, because PHLContracts uses two.
    #
    # **Blocks** are what a live run found, and they are not a grid:
    #
    #     Item: 1  072-08
    #     42831-002-156  FREIGHTLINER 114SD CHASSIS AS PER DFS SPEC 25026CNGb.25
    #
    # — a cell naming the item and its NIGP class-item, beside a cell holding a
    # commodity code and the description, with any quantity and unit written as
    # labelled text in the rows beneath. There is no header row at all.
    #
    # **Grids** are the ordinary header-and-columns table, read by column name.
    #
    # The grid header test is deliberately strict: an earlier version matched
    # `(item|line)` anywhere in a cell and any of `qty|unit|spec|…` in another,
    # which a *data* row satisfies — "Item: 1 072-08" starts with "item", and
    # "…AS PER DFS SPEC 25026CNGb.25" contains "spec". The first data row was
    # taken as the header, and every bid reported zero items. So a header cell
    # now has to *be* a label rather than contain one: matched whole, and short,
    # because no column heading is sixty characters of description.
    _JS_ITEMS = r"""
    const clean = (el) => ((el ? el.textContent : '') || '').replace(/\\s+/g, ' ').trim();
    const tableOf = (node) => (node ? node.closest('table') : null);
    const ownRows = (t) => [...t.querySelectorAll('tr')].filter((tr) => tableOf(tr) === t);
    const ownCells = (tr) =>
      [...tr.querySelectorAll('td, th')].filter((c) => c.closest('tr') === tr);
    const norm = (s) => (s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

    const ITEM_HEADER = /^(item|line)( (#|no|nbr|number|num))?$/;
    const FIELD_HEADER = new RegExp('^(qty|quantity|unit|units|uom|unit of measure|' +
      'description|item description|commodity|commodity code|nigp|nigp code|' +
      'unit price|unit cost|price|cost|spec|specification|extended|amount|total)$');
    const HEADER_MAX = 40;          // a heading, not a paragraph

    const isHeaderRow = (cells) => {
      const labels = cells.map((c) => norm(clean(c)));
      if (!labels.some((l) => l.length <= HEADER_MAX && ITEM_HEADER.test(l))) return false;
      return labels.some((l) => l.length <= HEADER_MAX && FIELD_HEADER.test(l));
    };

    // -- (1) a grid, if the page has one -------------------------------------
    let table = null, headers = [];
    for (const candidate of document.querySelectorAll('table')) {
      for (const tr of ownRows(candidate)) {
        const cells = ownCells(tr);
        if (cells.length < 2) continue;
        if (isHeaderRow(cells)) { table = candidate; headers = cells.map((c) => norm(clean(c))); break; }
      }
      if (table) break;
    }

    if (table) {
      const columnFor = (...names) => {
        for (const name of names) {
          const i = headers.findIndex((h) => h.includes(name));
          if (i >= 0) return i;
        }
        return -1;
      };
      const index = {
        item_number: columnFor('item', 'line'),
        name: columnFor('description', 'item name', 'commodity'),
        quantity: columnFor('quantity', 'qty'),
        unit: columnFor('unit of measure', 'uom', 'unit'),
        unit_price: columnFor('unit price', 'unit cost', 'price', 'cost'),
        nigp_code: columnFor('nigp', 'commodity code'),
      };
      const at = (cells, i) => (i >= 0 && i < cells.length ? clean(cells[i]) : '');

      const items = [];
      let headerSeen = false;
      for (const tr of ownRows(table)) {
        if (tr.querySelector('table')) continue;
        const cells = ownCells(tr);
        if (!headerSeen) { if (isHeaderRow(cells)) headerSeen = true; continue; }
        const text = clean(tr);
        if (!text) continue;
        if (cells.length < 2) {
          if (items.length) {
            const last = items[items.length - 1];
            last.specification = (last.specification ? last.specification + ' ' : '') + text;
          }
          continue;
        }
        const item = {
          item_number: at(cells, index.item_number),
          name: at(cells, index.name),
          quantity: at(cells, index.quantity),
          unit: at(cells, index.unit),
          unit_price: at(cells, index.unit_price),
          nigp_code: at(cells, index.nigp_code),
          specification: '',
        };
        if (Object.values(item).some((v) => v)) items.push(item);
      }
      if (items.length) return {found: true, items: items, headers: headers, strategy: 'grid'};
    }

    // -- (2) the block layout -------------------------------------------------
    // "Item: 1  072-08" opens an item. Everything until the next one belongs to
    // it: the description beside it, and any labelled quantity/unit/price under
    // it. Anchored at the start so a description mentioning an item elsewhere
    // cannot open a phantom.
    const ITEM_OPENER = /^item\s*:?\s*(\d+)\b/i;
    const NIGP = /\b(\d{3})\s*-\s*(\d{2})\b/;
    const LABELLED = (text, ...names) => {
      for (const name of names) {
        const m = text.match(new RegExp(name + '\\s*:?\\s*([^:]+?)(?:\\s{2,}|$|\\s+[A-Z][a-z]+\\s*:)', 'i'));
        if (m && m[1] && m[1].trim()) return m[1].trim();
      }
      return '';
    };

    const blocks = [];
    for (const tr of document.querySelectorAll('tr')) {
      if (tr.querySelector('table')) continue;
      const cells = ownCells(tr);
      if (!cells.length) continue;
      const texts = cells.map((c) => clean(c)).filter((t) => t);
      if (!texts.length) continue;

      const opener = texts.find((t) => ITEM_OPENER.test(t));
      if (opener) {
        const nigp = opener.match(NIGP);
        // The description is the longest other cell — the opener cell holds the
        // item number and its class code, never the wording.
        const rest = texts.filter((t) => t !== opener)
                          .sort((a, b) => b.length - a.length);
        let name = rest[0] || '';
        let commodity = '';
        const coded = name.match(/^([0-9][0-9\s-]{4,})\s+(\S.*)$/);
        if (coded) { commodity = coded[1].trim(); name = coded[2].trim(); }
        blocks.push({
          item_number: opener.match(ITEM_OPENER)[1],
          name: name,
          commodity_code: commodity,
          nigp_code: nigp ? nigp[1] + '-' + nigp[2] : '',
          quantity: '', unit: '', unit_price: '', specification: '',
        });
        continue;
      }
      if (!blocks.length) continue;
      const line = texts.join(' ');
      const item = blocks[blocks.length - 1];
      item.quantity = item.quantity || LABELLED(line, 'quantity', 'qty');
      item.unit = item.unit || LABELLED(line, 'unit of measure', 'uom', 'unit');
      item.unit_price = item.unit_price || LABELLED(line, 'unit price', 'unit cost');
      if (!/^(quantity|qty|uom|unit)\b/i.test(line)) {
        item.specification = (item.specification ? item.specification + ' ' : '') + line;
      }
    }
    if (blocks.length) return {found: true, items: blocks, headers: [], strategy: 'blocks'};
    return {found: false, items: [], headers: [], strategy: ''};
    """

    def scrape_items(self, bid_number: str) -> list[dict[str, str]]:
        """The bid's line items, or [] when the portal published none.

        An empty list is an ordinary answer here — plenty of bids describe the
        work in their attachments rather than in an item table — so it is logged
        as a count rather than treated as a failure.
        """
        try:
            result = self.driver.execute_script(self._JS_ITEMS) or {}
        except WebDriverException as exc:
            logger.warning("[run %s] %s: item table not read (%s)",
                           self.run_id, bid_number, exc.__class__.__name__)
            return []

        items = [item for item in result.get("items") or [] if any(item.values())]
        if result.get("found"):
            # The strategy is logged because the two are read very differently,
            # and "blocks" on a page that should be a grid is the first sign the
            # portal has changed shape again.
            detail = ", ".join(result.get("headers") or []) or "no header row"
            logger.info(" ├── Line Items: %d found (%s: %s)",
                        len(items), result.get("strategy") or "unknown", detail)
        else:
            logger.info(" ├── Line Items: the page carries no item table")
        return items

    def _write_items_file(self, record: dict[str, Any]) -> None:
        """Write `bid_items_details.txt` into the bid's folder.

        Written for every bid, including one with no items: a folder of PDFs and
        nothing naming the bid they belong to is what this file exists to fix.
        """
        path = storage.items_path(self.run_dir, record["bid_number"])
        try:
            path.write_text(details.render_items_text(record), encoding="utf-8")
        except OSError as exc:
            # Not fatal — the documents and the spreadsheet are the deliverable
            # and both survive this — but never silent.
            logger.warning("[run %s] %s: could not write %s (%s)",
                           self.run_id, record["bid_number"],
                           storage.ITEMS_FILENAME, exc.__class__.__name__)

    def scrape_detail(self, record: dict[str, Any]) -> None:
        """Open one bid, read its header and items, and save its attachments.

        Everything is written onto `record` in place. A bid whose detail page
        cannot be read keeps its listing fields and carries the error — it stays
        in the report rather than vanishing from it.
        """
        bid_number = record["bid_number"]
        self.set_step(f"reading_bid:{bid_number}")
        logger.info("[DETAIL PAGE - ATTACHMENTS]:")
        logger.info(" ├── Target URL: %s", record["detail_url"])
        self.navigate(record["detail_url"])
        time.sleep(SETTLE_SECONDS)

        try:
            header = self.driver.execute_script(self._JS_HEADER) or {}
        except WebDriverException as exc:
            header = {}
            record["error"] = f"header not read ({exc.__class__.__name__})"
            logger.warning("[run %s] %s: header not read", self.run_id, bid_number)
        record["extra_header_data"] = header

        # The header table reaches the reader as spreadsheet columns rather than
        # as a JSON file beside the documents: three fields promoted to columns
        # of their own, the rest folded into one readable cell.
        record.update(details.promote_header(header))
        record["additional_header"] = details.additional_header(header)

        record["items"] = self.scrape_items(bid_number)
        record["item_count"] = len(record["items"])

        folder = storage.bid_folder(self.run_dir, bid_number)
        self._write_items_file(record)

        saved, errors = self.download_attachments(
            bid_number, folder, record["detail_url"]
        )

        # The count that reaches the spreadsheet is the count of files actually
        # in the bid's folder — not how many links the page offered, and not how
        # many clicks were made. A number taken from anywhere but the disk can
        # say five while the client opens the folder and finds three.
        on_disk = storage.saved_documents(self.run_dir, bid_number)
        expected = len(saved) + len(errors)
        logger.info(" └── [VERIFIED]: %d/%d file(s) saved to folder %s/",
                    len(on_disk), expected, storage.folder_reference(bid_number))
        if len(on_disk) != len(saved):
            # The two disagreeing means a file moved out from under the run, or
            # one landed that was never counted. Either way the disk wins.
            logger.warning(
                "[run %s] %s: %d file(s) were saved but %d are in the folder — "
                "the folder is what the sheet will report",
                self.run_id, bid_number, len(saved), len(on_disk),
            )

        record["file_paths"] = [
            f"{storage.folder_reference(bid_number)}/{name}" for name in on_disk
        ]
        record["documents_downloaded"] = len(on_disk)
        record["document_errors"] = errors
        self._documents_downloaded += len(on_disk)
        self._documents_failed += len(errors)

    # Every File Attachment on the detail page, as name and file id. The id is
    # the one in `javascript:editFile('391619')` — the anchor's identity, and
    # the only thing about it that survives the page being redrawn. It is also
    # what de-duplicates the list: some bids render the same attachment anchor
    # more than once, and a name read twice would be downloaded twice.
    _JS_ATTACHMENTS = """
    const clean = (el) => ((el ? el.textContent : '') || '').replace(/\\s+/g, ' ').trim();
    const out = [];
    const seen = new Set();
    for (const a of document.querySelectorAll("a[href*='editFile']")) {
      const href = a.getAttribute('href') || '';
      const match = href.match(/editFile\\(\\s*['"]([^'"]+)['"]/);
      if (!match) continue;
      const id = match[1];
      if (seen.has(id)) continue;
      seen.add(id);
      out.push({file_id: id, name: clean(a)});
    }
    return out;
    """

    def download_attachments(
        self, bid_number: str, folder: Path, detail_url: str | None = None
    ) -> tuple[list[str], list[str]]:
        """Save every File Attachment for this bid. Returns (saved, errors).

        The anchors carry `javascript:editFile('391619')` and no URL, so each is
        clicked and the browser's download waited for. The click is done through
        JS: these pages are wide tables and a native click can land on a cell
        that has scrolled over the link.

        Each attachment is found again by its file id rather than by its place
        in the list. Clicking one of these links can leave the detail page or
        redraw it, and a positional lookup reads whatever anchor now sits at that
        index — or, once the list is shorter than the index, reports a file that
        is still on the page as missing. That is what "the link was no longer on
        the page" meant: not a vanished attachment, but a list that had moved
        underneath the loop.

        A file that does not arrive is named in the errors rather than skipped
        quietly — an attachment list that is silently one short is worse than one
        that says which is missing.
        """
        attachments = self._await_attachments()
        if not attachments:
            return [], []
        logger.info(" ├── Detected Attachments: %d file%s found on page.",
                    len(attachments), "" if len(attachments) == 1 else "s")

        saved: list[str] = []
        errors: list[str] = []
        for index, item in enumerate(attachments, start=1):
            name = item.get("name") or f"attachment_{index}"
            position = f"({index}/{len(attachments)})"
            try:
                target = self._download_one(item["file_id"], folder, index, detail_url)
                saved.append(target)
                logger.info(" ├── Downloading: %s %s -> Success", name, position)
            except (TimeoutException, WebDriverException, OSError) as exc:
                # Named, not swallowed: a timeout here is a document the client
                # will not find in the ZIP, and a run that hides it looks
                # complete while being short.
                detail = (getattr(exc, "msg", None) or str(exc) or "").strip()
                errors.append(f"{name}: {exc.__class__.__name__}")
                logger.warning(" ├── Downloading: %s %s -> FAILED (%s%s)",
                               name, position, exc.__class__.__name__,
                               f": {detail[:120]}" if detail else "")
        if errors:
            run_manager.add_warning(
                self.run_id,
                f"{bid_number}: {len(errors)} of {len(attachments)} attachment(s) could "
                f"not be saved — {'; '.join(errors)[:300]}",
            )
        return saved, errors

    def _attachment_list(self) -> list[dict[str, str]]:
        """The bid's attachments as `{file_id, name}`, in page order."""
        try:
            return self.driver.execute_script(self._JS_ATTACHMENTS) or []
        except WebDriverException as exc:
            logger.warning("[run %s] could not read the attachment list (%s)",
                           self.run_id, exc.__class__.__name__)
            return []

    def _await_attachments(self) -> list[dict[str, str]]:
        """The attachment list, once the page has finished rendering it.

        The File Attachments row is written by the page rather than served with
        it, so reading it the moment the detail page loads can catch three of
        five anchors. This waits for the first one, then for the count to stop
        growing — a list read while it is still being built is the difference
        between five documents and three.
        """
        try:
            self.wait(ATTACHMENT_TIMEOUT).until(
                EC.presence_of_element_located((By.XPATH, ATTACHMENT_LINK_XPATH))
            )
        except TimeoutException:
            # Plenty of bids carry no attachments at all. That is an empty list,
            # not an error — the caller records zero documents and moves on.
            return []

        attachments = self._attachment_list()
        for _ in range(ATTACHMENT_SETTLE_READS):
            time.sleep(0.5)
            again = self._attachment_list()
            if len(again) <= len(attachments):
                return again if len(again) == len(attachments) else attachments
            attachments = again
        logger.info("[run %s] the attachment list was still growing after %d reads "
                    "— taking the %d found",
                    self.run_id, ATTACHMENT_SETTLE_READS, len(attachments))
        return attachments

    def _download_one(
        self, file_id: str, folder: Path, index: int, detail_url: str | None
    ) -> str:
        """Click one attachment, wait for it to land, and file it. Returns its name.

        The click is retried once. The first attempt can fail for reasons that do
        not repeat — the servlet was slow, the page had moved on — and reopening
        the bid puts the link back where it can be clicked.
        """
        last: Exception | None = None
        for attempt in (1, 2):
            # What is already staged, so the wait cannot mistake the previous
            # download for this one and return before the file has arrived.
            staged = {p for p in self.download_dir.iterdir() if p.is_file()}
            link = self._attachment_anchor(file_id, detail_url)
            if link is None:
                raise WebDriverException("the link is not on the detail page")
            self.scroll_into_view(link)
            self.driver.execute_script("arguments[0].click();", link)
            try:
                downloaded = self.wait_for_download(ignore=staged)
                break
            except TimeoutException as exc:
                last = exc
                if attempt == 2 or not detail_url:
                    raise
                logger.info("[run %s] attachment %s did not arrive — reopening the "
                            "bid and trying once more", self.run_id, file_id)
                self.navigate(detail_url)
                time.sleep(SETTLE_SECONDS)
        else:  # pragma: no cover - the loop either breaks or raises
            raise last or TimeoutException("download did not complete")

        target = folder / downloaded.name
        if target.exists():
            target = folder / f"{index}_{downloaded.name}"
        shutil.move(str(downloaded), str(target))
        return target.name

    def _attachment_anchor(self, file_id: str, detail_url: str | None):
        """The anchor for one attachment, or None if it is genuinely not there.

        A download can leave the detail page behind — the click goes through the
        servlet, and some bids come back on a different page than they left. So a
        link that is missing is not believed the first time: the detail page is
        loaded again and asked once more, which is the difference between four
        attachments lost and four attachments saved.
        """
        # The ids the portal issues are numeric, so this cannot escape the
        # predicate; the quoting is chosen so an id never has to be escaped.
        xpath = ATTACHMENT_BY_ID_XPATH.format(file_id=file_id)
        try:
            found = self.driver.find_elements(By.XPATH, xpath)
        except WebDriverException:
            found = []
        if found:
            return found[0]
        if not detail_url:
            return None

        logger.info("[run %s] attachment %s is not on the current page — reopening "
                    "the bid", self.run_id, file_id)
        try:
            self.navigate(detail_url)
            time.sleep(SETTLE_SECONDS)
            found = self.driver.find_elements(By.XPATH, xpath)
        except WebDriverException:
            return None
        return found[0] if found else None

    # -- orchestration --------------------------------------------------------

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        self._save_run_row()
        try:
            self.start_driver()
            self.login()
            # Two ways in, one way on: an Advanced Search and the Open Bids list
            # both end at the same results table, so everything after this line
            # is the same work over whichever set of bids arrived.
            if self.filters:
                self.open_bids_list()
                self.advanced_search(self.filters)
            else:
                self.open_bids_list()
            self._records = self.collect_all_pages()

            if not self._records:
                message = (
                    f"no bids matched this search ({search.describe(self.filters)})"
                    if self.filters else "the portal listed no open bids"
                )
                run_manager.add_warning(self.run_id, message)
                run_manager.update_run(self.run_id, no_results=True)

            logger.info("[SEARCH EXECUTED]: %d open bid(s) detected.", len(self._records))
            for index, record in enumerate(self._records, start=1):
                run_manager.update_run(
                    self.run_id,
                    step=f"bid ({index}/{len(self._records)}): {record['bid_number']}",
                    bids_processed=index,
                )
                try:
                    self.scrape_detail(record)
                except StopRequested:
                    raise
                except (TimeoutException, WebDriverException) as exc:
                    record["error"] = f"{exc.__class__.__name__}: {str(exc)[:200]}"
                    logger.exception("[run %s] %s failed", self.run_id, record["bid_number"])
                    run_manager.add_error(
                        self.run_id,
                        f"{record['bid_number']}: {exc.__class__.__name__}",
                    )
                logger.info(
                    " └── [BID %d/%d]: %s — %d document(s), %d header field(s).",
                    index, len(self._records), record["bid_number"],
                    record.get("documents_downloaded", 0),
                    len(record.get("extra_header_data") or {}),
                )

            run_manager.update_run(
                self.run_id,
                bids_found=len(self._records),
                bids_processed=len(self._records),
                documents_downloaded=self._documents_downloaded,
            )
            self._mirror_to_run_state()

            # The summary sheet goes to disk first and always: the deliverable is
            # an archive whose root holds it beside the documents it indexes, so
            # it has to exist at packaging time whatever the database is doing.
            self.set_step("generating_excel")
            summary = storage.summary_path(self.run_dir)
            try:
                written = export.generate_excel_from_records(self._records, summary)
                run_manager.update_run(
                    self.run_id, excel_path=str(summary), excel_exported=True
                )
                logger.info("[run %s] summary sheet holds %d row(s)", self.run_id, written)
            except Exception:  # noqa: BLE001 — never fail a run over the workbook
                logger.exception("[run %s] summary sheet failed", self.run_id)
                run_manager.add_error(self.run_id, "summary sheet could not be written")

            self.set_step("storing_in_db")
            try:
                run = run_manager.get_run(self.run_id) or {"run_id": self.run_id}
                stored = export.save_bids(run, self._records)
                run_manager.update_run(self.run_id, bids_stored_in_db=stored)
            except Exception:  # noqa: BLE001 — the files on disk are the record
                logger.exception("[run %s] DB save failed", self.run_id)
                run_manager.add_error(self.run_id, "db save failed (see logs)")
                # Tells the download/email path to serve the sheet on disk rather
                # than regenerate an empty one from a DB that never got the rows.
                run_manager.update_run(self.run_id, db_save_failed=True)

            self.set_step("packaging_results")
            archive_run(self.run_id)

            logger.info(
                "[PIPELINE COMPLETE]: Total Processed: %d / Total Detected: %d "
                "| %d document(s) saved, %d failed",
                len(self._records), len(self._records),
                self._documents_downloaded, self._documents_failed,
            )
            run_manager.update_run(self.run_id, status="completed", step="done")
            notify_scrape_completion(self.run_id, "philadelphia", len(self._records))
        except StopRequested:
            # The user pressed Stop. run_manager has already locked the run to
            # "stopped" and suppresses later writes, so there is nothing to
            # record — and this must not fall through to the handler below,
            # which would log a traceback and screenshot a closed browser.
            logger.info("[run %s] stopped by user", self.run_id)
        except Exception as exc:  # noqa: BLE001 — a failed run is reported, not crashed
            logger.exception("[run %s] failed", self.run_id)
            self.screenshot("fatal")
            run_manager.add_error(self.run_id, str(exc)[:500])
            run_manager.update_run(self.run_id, status="failed", step="failed")
        finally:
            self.cleanup()
            run_manager.update_run(self.run_id, finished_at=datetime.now().isoformat())
            self._save_run_row()
            run_manager.remove_empty_folder(self.run_id)

    def _mirror_to_run_state(self) -> None:
        """Put the scraped bids on the run for the console's table."""
        if len(self._records) > LIVE_PREVIEW_CEILING:
            logger.warning(
                "[run %s] showing the first %d of %d bids in the console; the "
                "spreadsheet and the database hold all of them",
                self.run_id, LIVE_PREVIEW_CEILING, len(self._records),
            )
        for record in self._records[:LIVE_PREVIEW_CEILING]:
            run_manager.add_bid_result(self.run_id, {
                "bid_number": record.get("bid_number"),
                "title": record.get("description"),
                "buyer": record.get("buyer"),
                "close_date": record.get("bid_opening_date"),
                "document_count": record.get("documents_downloaded", 0),
                "folder": storage.folder_reference(record.get("bid_number") or ""),
                "error": record.get("error"),
            })

    def _save_run_row(self) -> None:
        run = run_manager.get_run(self.run_id)
        if not run:
            return
        try:
            export.save_run(run)
        except Exception:  # noqa: BLE001 — the run must not fail over its own row
            logger.exception("[run %s] save_run failed", self.run_id)


def execute_run(run_id: str) -> None:
    PhiladelphiaScraper(run_id).run()
