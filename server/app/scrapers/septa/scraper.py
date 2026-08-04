"""Selenium automation for the SEPTA vendor procurement portal.

One run, one search:

    login
    go straight to the Open Quotes search form
    Open Date Range given?  -> fill it       <- entirely optional
                   not given -> type nothing
    click Search
    page through the grid, skipping blacklisted summaries
    store what's left in the DB, export one sheet

**Keyword and commodity-code searching is gone.** The portal's Open Quotes grid
is a parts-requisition feed, and searching it term by term (a niche's keywords,
then its NIGP codes) returned a small, unreliable slice of it — the checklist's
keywords legitimately match nothing here, and its commodity codes were never
verified. Fetching the grid whole and filtering it locally is both simpler and
more complete, so the niche catalog, the per-term search loop, and the
re-navigation machinery that loop needed are all removed.

The Open Date Range is **optional and has no default**. The previous scraper
substituted today's date whenever a run carried no other filter, which quietly
narrowed an unfiltered run to a single day. No dates now means no date typing
at all, which is what returns every open quote.

Summaries naming an out-of-scope manufacturer are dropped during parsing —
before evaluation, before the DB, before the sheet. See `exclusions.py`, which
owns that list and the whole-word matching it requires. That blacklist is the
**only** thing that removes a quote here.

In particular there is **no close-date window**. SEPTA used to apply the shared
`MIN_DAYS_UNTIL_CLOSE` (7 days) rule and drop anything closing sooner, which
silently withheld the most urgent quotes in the grid. Every open quote is now
kept and its close date exported as scraped. This is a SEPTA-only departure:
`app/core/closing_filter` still defines the rule and every other portal applies
it unchanged.
"""

import logging
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.config import settings
from app.core import run_manager
from app.core.exports import archive_run
from app.services.notifier import notify_scrape_completion
from app.core.base_scraper import BaseScraper
from app.core.filenames import sanitize_filename
from app.scrapers.septa import exclusions, export
from app.scrapers.septa.filters import BadDate, OpenDateRange

logger = logging.getLogger(__name__)

# -- timeouts (seconds) ------------------------------------------------------
LOGIN_FIELD_WAIT = 30       # per-field wait for the login form to render
LOGIN_SETTLE_SLEEP = 2      # let ASP.NET/Bootstrap finish wiring the form
LOGIN_REDIRECT_WAIT = 45    # postback + redirect can be slow
NAVIGATION_SLEEP = 3
SEARCH_FORM_WAIT = 20       # for the search form's Search button to render
SEARCH_RESULT_WAIT = 20
DATE_FIELD_WAIT = 5
NEXT_PAGE_WAIT = 10
PAGE_CHANGE_SLEEP = 2

# Pagination safety cap. Raised from 50 now that a run fetches the whole Open
# Quotes grid rather than a niche's slice of it: the unfiltered grid is far
# longer than any single term's results, and hitting the cap silently truncates
# the run's output. Reaching it is still reported as an error, because a run
# that stops early should say so rather than look complete.
MAX_PAGES = 200
PREVIEW_LIMIT = 100   # rows mirrored to the live run state for the UI table

# SEPTA does NOT apply the shared >=7-days-until-close rule
# (app/core/closing_filter). Every open quote is kept whatever its close date;
# the date is exported as scraped. The other portals still apply it.

# -- selectors (ported verbatim from the package config) ---------------------
SEL = {
    # Exact IDs as rendered by https://epsadmin.septa.org/vendor/login. Tried
    # before the fuzzy xpaths below, which also match hidden inputs belonging to
    # other ASP.NET panels on the same page.
    "username_id": "ctl00_ctl00_masterMain_cntMain_ctl00_txtUsername",
    "password_id": "ctl00_ctl00_masterMain_cntMain_ctl00_txtPassword",
    "login_btn_id": "ctl00_ctl00_masterMain_cntMain_ctl00_lbtnSubmit",
    # Lockout / attempt-countdown banner, checked BEFORE submitting so a locked
    # account is never handed another failed attempt.
    "lockout_xpath": (
        "//*[contains(text(), 'locked') or contains(text(), 'Locked') or "
        "contains(text(), 'more attempts')]"
    ),
    "username_xpath": (
        "//input[contains(@id, 'Username') or contains(@name, 'Username') or "
        "contains(@id, 'User') or contains(@name, 'User') or "
        "contains(@id, 'email') or contains(@name, 'email') or @type='email']"
    ),
    "username_label_xpath": (
        "//label[contains(text(), 'Login ID') or contains(text(), 'User')]/following::input[1]"
    ),
    "username_fallback_css": "input[type='text']",
    "password_xpath": (
        "//input[contains(@id, 'Password') or contains(@name, 'Password') or "
        "contains(@id, 'Pass') or contains(@name, 'Pass') or @type='password']"
    ),
    "password_label_xpath": "//label[contains(text(), 'Password')]/following::input[1]",
    "login_btn_xpath": (
        "//a[contains(text(), 'Submit')] | "
        "//a[contains(@href, 'doPostBack') and contains(text(), 'Submit')] | "
        "//a[contains(@id, 'Submit')] | "
        "//button[contains(text(), 'SUBMIT') or contains(text(), 'Submit')] | "
        "//input[@value='SUBMIT' or @value='Submit' or @type='submit']"
    ),
    "logout_xpath": "//a[contains(@href, 'logout')]",
    # The failure alert renders as separate lines ("Invalid Login: ...", plus a
    # "Warning: You have N more attempts before your account is locked for 60
    # minutes."). Match all of them so the run's error carries the lockout
    # countdown, not just "Invalid".
    "login_error_xpath": (
        "//*[contains(text(), 'Invalid') or contains(text(), 'Failed') or "
        "contains(text(), 'locked') or contains(text(), 'more attempts')]"
    ),
    # The Open Date Range pair. The "Opens" start box is the one the previous
    # scraper drove; its "to" counterpart follows the same ASP.NET naming.
    # Both stay tolerant of an id change, but neither is required — a run with
    # no dates never looks for them.
    "open_date_start_xpath": (
        "//*[@id='ctl00_ctl00_masterMain_cntMain_ctl00_txtOpensStartDate'] | "
        "//input[contains(@name, 'txtOpensStartDate')] | "
        "//input[contains(@id, 'OpenDate') or contains(@name, 'OpenDate') or "
        "contains(@id, 'FromDate') or contains(@name, 'FromDate') or "
        "contains(@id, 'StartDate') or contains(@name, 'StartDate')] | "
        "//input[contains(@class, 'date') and not(contains(@id, 'Close')) "
        "and not(contains(@id, 'End'))]"
    ),
    "open_date_end_xpath": (
        "//*[@id='ctl00_ctl00_masterMain_cntMain_ctl00_txtOpensEndDate'] | "
        "//input[contains(@name, 'txtOpensEndDate')] | "
        "//input[contains(@id, 'OpensEnd') or contains(@name, 'OpensEnd') or "
        "contains(@id, 'OpensToDate') or contains(@name, 'OpensToDate')]"
    ),
    "search_btn_xpath": (
        "//a[contains(text(), 'Search') or contains(text(), 'SEARCH')] | "
        "//a[contains(@id, 'Search') or contains(@id, 'btnSearch')] | "
        "//button[contains(text(), 'Search') or contains(text(), 'SEARCH')] | "
        "//input[@value='Search' or @value='SEARCH' or @type='submit'] | "
        "//*[@id='searchButton'] | //*[contains(@class, 'search-btn')]"
    ),
    "data_table_wait_xpath": "//table[contains(@class, 'data') or contains(@class, 'table') or @id]",
    "table_selectors": [
        "//table[contains(@class, 'data') or contains(@class, 'table')]",
        "//table[@id]",
        "//table[.//th]",
        "//div[contains(@class, 'table')]//table",
        "//*[@role='table' or @role='grid']",
    ],
    "next_page_selectors": [
        "//a[contains(text(), 'Next')]",
        "//a[contains(text(), 'next')]",
        "//a[text()='>']",
        "//a[contains(text(), ' > ')]",
        "//input[@class='next']",
        "//a[contains(@id, 'btnNext')]",
        "//a[contains(@id, 'Next')]",
    ],
}


class SeptaScraper(BaseScraper):
    def __init__(self, run_id: str, dates: OpenDateRange | None = None):
        super().__init__(run_id)
        # The run's only search filter, and it is optional: an empty range means
        # the date boxes are never touched and the search returns every open
        # quote. There is no keyword, commodity code or niche any more.
        self.dates = dates or OpenDateRange()
        self.excel_path: Path | None = None
        # Full in-memory copy of every scraped row — the Excel fallback source if
        # the DB is unavailable.
        self._records: list[dict[str, Any]] = []
        # requisition number -> its record in _records. The grid repeats rows
        # across pages, so dedup stays run-level.
        self._seen: dict[str, dict[str, Any]] = {}
        # Rows mirrored to the live run state for the UI table.
        self._preview: list[dict[str, Any]] = []
        # Summary-blacklist tallies: how many quotes were skipped, and by which
        # term. A filter that removes rows without saying which rule fired is
        # indistinguishable from a scrape that simply missed them.
        self._excluded_by_summary = 0
        self._exclusion_reasons: Counter[str] = Counter()

    # -- selenium helpers (mirror the package's BrowserManager) -------------

    def _find(self, by, selector: str, timeout: int) -> Any | None:
        try:
            return WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((by, selector))
            )
        except TimeoutException:
            logger.warning("[run %s] element not found: %s", self.run_id, selector)
            return None

    def _find_clickable(self, by, selector: str, timeout: int) -> Any | None:
        try:
            return WebDriverWait(self.driver, timeout).until(
                EC.element_to_be_clickable((by, selector))
            )
        except TimeoutException:
            return None

    def _safe_click(self, element) -> bool:
        try:
            element.click()
            return True
        except ElementClickInterceptedException:
            try:
                self.driver.execute_script("arguments[0].click();", element)
                return True
            except WebDriverException:
                return False
        except WebDriverException:
            return False

    def _fill_field(self, element, value: str) -> bool:
        """Type ``value`` into ``element`` and make sure it actually stuck.

        SEPTA's login is an ASP.NET page: the password box in particular tends
        to swallow ``send_keys`` (the field re-renders after the first postback,
        or is only interactable once focused), which historically left the
        password blank and locked the account. Focus + click, type, then verify
        the value landed; if it didn't, set it via JS and fire the input/change
        events the page listens for.
        """
        try:
            self._safe_click(element)
        except WebDriverException:
            pass
        try:
            element.clear()
            element.send_keys(value)
        except WebDriverException:
            pass

        try:
            if (element.get_attribute("value") or "") == value:
                return True
        except WebDriverException:
            pass

        # send_keys didn't land — force it through the DOM.
        try:
            self.driver.execute_script(
                "arguments[0].value = arguments[1];"
                "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                element,
                value,
            )
            return (element.get_attribute("value") or "") == value
        except WebDriverException:
            return False

    # -- login --------------------------------------------------------------

    def _abort_if_locked(self, when: str) -> None:
        """Raise if the page is showing a lockout or attempts-remaining banner.

        SEPTA locks the account for 60 minutes after a few bad logins, and while
        locked it rejects the correct password too. Retrying restarts the window,
        so a locked account has to stop the run rather than feed the counter.
        """
        messages: list[str] = []
        for el in self.driver.find_elements(By.XPATH, SEL["lockout_xpath"]):
            try:
                text = " ".join(el.text.split())
            except WebDriverException:
                continue
            if text and not any(text in seen for seen in messages):
                messages.append(text)
        if not messages:
            return

        detail = " ".join(messages)
        lowered = detail.lower()
        # "You have N more attempts before your account is locked" also contains
        # "locked", so the countdown warning has to be recognised FIRST or every
        # warning would be misread as a hard lock and abort the run.
        if "more attempts" in lowered:
            logger.warning("[run %s] SEPTA lockout warning (%s): %s",
                           self.run_id, when, detail)
            return
        if "lock" in lowered:
            self.screenshot("login_locked")
            raise WebDriverException(
                f"SEPTA account is locked ({when}) — wait for the lockout to expire "
                f"before running again; retrying now restarts the 60-minute window. "
                f"Portal said: {detail}"
            )
        logger.warning("[run %s] SEPTA login banner (%s): %s",
                       self.run_id, when, detail)

    def login(self) -> None:
        """Sign in to the SEPTA vendor portal.

        Called by ``run()``. This must stay a method in its own right — it was
        once folded into ``_abort_if_locked``, which meant ``run()`` never had a
        ``login`` to call at all and every run died before typing a character.
        """
        self.set_step("logging_in")
        logger.info("[run %s] navigating to %s", self.run_id, settings.septa_login_url)
        self.driver.get(settings.septa_login_url)

        # Let the ASP.NET page finish rendering before touching the form.
        time.sleep(LOGIN_SETTLE_SLEEP)

        # Refuse to spend an attempt on an account the portal has already locked
        # — a locked account rejects the correct password too, and each retry
        # restarts the 60-minute window.
        self._abort_if_locked("before submitting")

        username_field = self._find(By.ID, SEL["username_id"], LOGIN_FIELD_WAIT)
        if not username_field:
            logger.warning(
                "[run %s] username not found by exact ID %s — falling back to xpath",
                self.run_id, SEL["username_id"],
            )
            username_field = self._find(By.XPATH, SEL["username_xpath"], 10)
        if not username_field:
            try:
                username_field = self.driver.find_element(By.XPATH, SEL["username_label_xpath"])
            except WebDriverException:
                username_field = self._find(By.CSS_SELECTOR, SEL["username_fallback_css"], 5)
        if not username_field:
            self.screenshot("login_no_username")
            raise WebDriverException("SEPTA login: could not find the username field.")

        password_field = self._find(By.ID, SEL["password_id"], LOGIN_FIELD_WAIT)
        if not password_field:
            logger.warning(
                "[run %s] password not found by exact ID %s — falling back to xpath",
                self.run_id, SEL["password_id"],
            )
            password_fields = self.driver.find_elements(By.XPATH, SEL["password_xpath"])
            if not password_fields:
                try:
                    password_fields = [
                        self.driver.find_element(By.XPATH, SEL["password_label_xpath"])
                    ]
                except WebDriverException:
                    self.screenshot("login_no_password")
                    raise WebDriverException("SEPTA login: could not find the password field.")
            # Prefer a visible, interactable field — the raw XPath can match hidden
            # ASP.NET inputs that silently drop send_keys.
            password_field = next(
                (f for f in password_fields if f.is_displayed()), password_fields[0]
            )

        if not self._fill_field(username_field, settings.septa_username):
            logger.warning("[run %s] username field may not have been filled", self.run_id)
        if not self._fill_field(password_field, settings.septa_password):
            self.screenshot("login_no_password_value")
            raise WebDriverException(
                "SEPTA login: could not enter the password into the field."
            )

        # Record what is actually in the form at submit time. Lengths only —
        # never the values. Without this, "wrong password" and "empty password
        # box" are indistinguishable in the logs, since the portal reports both
        # as invalid credentials.
        u_len = len(username_field.get_attribute("value") or "")
        p_len = len(password_field.get_attribute("value") or "")
        logger.info(
            "[run %s] submitting login — username=%s chars (expected %s), "
            "password=%s chars (expected %s)",
            self.run_id, u_len, len(settings.septa_username or ""),
            p_len, len(settings.septa_password or ""),
        )
        if p_len == 0:
            self.screenshot("login_password_empty")
            raise WebDriverException(
                "SEPTA login: the password box was empty at submit time — aborting "
                "rather than spending a failed attempt against the lockout counter."
            )

        login_button = self._find(By.ID, SEL["login_btn_id"], 10)
        if not login_button:
            login_button = self._find(By.XPATH, SEL["login_btn_xpath"], 5)
        if login_button:
            self._safe_click(login_button)
        else:
            # The submit control is an <a> whose href is a __doPostBack call;
            # invoking it directly works even when the anchor is unclickable.
            logger.warning("[run %s] submit link not found — calling __doPostBack",
                           self.run_id)
            self.driver.execute_script(
                "__doPostBack('ctl00$ctl00$masterMain$cntMain$ctl00$lbtnSubmit','');"
            )

        # Wait for the redirect away from the login page.
        try:
            WebDriverWait(self.driver, LOGIN_REDIRECT_WAIT).until(
                lambda d: (
                    "login" not in d.current_url.lower()
                    or d.find_elements(By.XPATH, SEL["logout_xpath"])
                )
            )
        except TimeoutException:
            logger.warning("[run %s] timeout waiting for login redirect", self.run_id)

        if "login" in self.driver.current_url.lower() and not self.driver.find_elements(
            By.XPATH, SEL["logout_xpath"]
        ):
            errors = self.driver.find_elements(By.XPATH, SEL["login_error_xpath"])
            messages: list[str] = []
            for el in errors:
                text = " ".join(el.text.split())
                # Ancestors match too and repeat their children's text; keep the
                # first (outermost) wording and drop anything already covered.
                if text and not any(text in seen for seen in messages):
                    messages.append(text)
            detail = f" Portal said: {' '.join(messages)}" if messages else ""
            self.screenshot("login_failed")
            raise WebDriverException(
                "SEPTA login did not complete — check the SEPTA credentials in "
                f"server/.env.{detail}"
            )
        logger.info("[run %s] login successful", self.run_id)

    # -- navigation ---------------------------------------------------------

    def navigate_to_open_quotes(self) -> None:
        """Load the Open Quotes search form, straight to its URL.

        A run is one search, so this happens once. The old link-text / href /
        menu-crawling heuristics are gone: they never matched this portal's
        actual anchor ("Search Open Quotes", not "View Open Quotes") and only
        ever ran after the direct URL had already succeeded.
        """
        self.set_step("opening_open_quotes")
        try:
            self.driver.get(settings.septa_search_url)
        except WebDriverException as exc:
            self.screenshot("open_quotes_unreachable")
            raise WebDriverException(
                f"SEPTA: could not load the Open Quotes search form "
                f"({settings.septa_search_url}) — {exc.__class__.__name__}."
            ) from exc

        # The Search button is the form's signature. The results list the search
        # lands on carries no filter inputs, so this also distinguishes the two
        # pages — the job the keyword box used to do before it was removed.
        if self._find(By.XPATH, SEL["search_btn_xpath"], SEARCH_FORM_WAIT) is None:
            self.screenshot("open_quotes_no_form")
            raise WebDriverException(
                "SEPTA: reached the Open Quotes URL but its search form never "
                "rendered — the portal may have moved it."
            )
        logger.info("[run %s] reached the Open Quotes search form", self.run_id)

    # -- optional date range + search ---------------------------------------

    def _visible_field(self, xpath: str):
        """The first visible input matching `xpath`, freshly located."""
        for field in self.driver.find_elements(By.XPATH, xpath):
            try:
                if field.is_displayed():
                    return field
            except WebDriverException:
                continue
        return None

    def _fill_date(self, xpath: str, value: str, label: str) -> bool:
        """Type one date into the form, verifying it landed.

        Routed through `_fill_field` for the same reason the login password is:
        these inputs sit on an ASP.NET panel that silently swallows send_keys
        after a postback.
        """
        field = self._visible_field(xpath)
        if field is None:
            logger.warning("[run %s] no %s input on the form", self.run_id, label)
            return False
        return self._fill_field(field, value)

    def apply_date_range(self) -> None:
        """Fill the Open Date Range — or deliberately do nothing.

        Nothing is the normal case. When the run carries no dates the boxes are
        never located or typed into, and Search then returns every open quote.
        A date that will not parse is reported and skipped rather than replaced
        with a guess, so the run widens to everything instead of silently
        searching a day nobody asked for.
        """
        if self.dates.is_empty:
            logger.info(
                "[run %s] no Open Date Range given — searching all open quotes",
                self.run_id,
            )
            return

        self.set_step("applying_date_range")
        try:
            start, end = self.dates.portal_values()
        except BadDate as exc:
            logger.warning("[run %s] %s — searching unfiltered instead", self.run_id, exc)
            run_manager.add_warning(
                self.run_id,
                f"could not read the {exc.field} date {exc.value!r} (expected YYYY-MM-DD) "
                "— searched all open quotes instead",
            )
            return

        for value, xpath, label in (
            (start, SEL["open_date_start_xpath"], "Open Date Range 'from'"),
            (end, SEL["open_date_end_xpath"], "Open Date Range 'to'"),
        ):
            if value is None:
                continue
            if self._fill_date(xpath, value, label):
                logger.info("[run %s] %s = %s", self.run_id, label, value)
            else:
                run_manager.add_warning(
                    self.run_id, f"could not enter the {label} ({value})"
                )

    def search(self) -> None:
        """Click Search and wait for the results grid."""
        self.set_step("searching")
        search_btn = self._find(By.XPATH, SEL["search_btn_xpath"], DATE_FIELD_WAIT)
        if not search_btn:
            self.screenshot("no_search_button")
            raise WebDriverException("SEPTA: could not find the Open Quotes search button.")
        self._safe_click(search_btn)

        try:
            WebDriverWait(self.driver, SEARCH_RESULT_WAIT).until(
                EC.presence_of_element_located((By.XPATH, SEL["data_table_wait_xpath"]))
            )
        except TimeoutException:
            logger.warning("[run %s] timeout waiting for the results table", self.run_id)

    # -- scraping -----------------------------------------------------------

    def _find_data_table(self):
        for selector in SEL["table_selectors"]:
            try:
                table = self.driver.find_element(By.XPATH, selector)
                if table.is_displayed():
                    return table
            except WebDriverException:
                continue
        return None

    def _extract_row(self, row) -> dict[str, str] | None:
        try:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) >= 4:
                return {
                    "requisition_number": cells[0].text.strip() or "N/A",
                    "summary": cells[1].text.strip(),
                    "open_date": cells[2].text.strip(),
                    "close_date": cells[3].text.strip(),
                }
            # Fallback: split the row's text on newlines.
            parts = [p.strip() for p in row.text.strip().split("\n") if p.strip()]
            if not parts:
                return None
            return {
                "requisition_number": parts[0],
                "summary": parts[1] if len(parts) > 1 else "",
                "open_date": parts[2] if len(parts) > 2 else "",
                "close_date": parts[3] if len(parts) > 3 else "",
            }
        except WebDriverException:
            return None

    def _scrape_page(self) -> list[dict[str, str]]:
        table = self._find_data_table()
        if not table:
            logger.warning("[run %s] no data table on this page", self.run_id)
            return []
        rows = table.find_elements(By.TAG_NAME, "tr")
        if len(rows) <= 1:
            return []
        out: list[dict[str, str]] = []
        for row in rows[1:]:
            rec = self._extract_row(row)
            if rec:
                out.append(rec)
        return out

    def _click_next_page(self) -> bool:
        for selector in SEL["next_page_selectors"]:
            try:
                btn = self.driver.find_element(By.XPATH, selector)
                if not (btn.is_displayed() and btn.is_enabled()):
                    continue
                if "disabled" in (btn.get_attribute("class") or "").lower():
                    continue
                if self._safe_click(btn):
                    try:
                        WebDriverWait(self.driver, NEXT_PAGE_WAIT).until(EC.staleness_of(btn))
                    except WebDriverException:
                        pass
                    return True
            except WebDriverException:
                continue
        return False

    def _record_quote(self, rec: dict[str, str]) -> bool:
        """Add one scraped row to the run. True when it is new and kept.

        This is the single gate every scraped row passes through on its way to
        the DB, the spreadsheet and the UI, so it is where the summary
        blacklist is applied — an excluded quote is never processed, never
        evaluated and never stored.
        """
        # The blacklist runs first: an out-of-scope quote should not consume a
        # dedup slot or be weighed against the close-date rule, and checking it
        # here keeps "skipped" and "closing soon" counting different things.
        excluded = exclusions.excluded_by(rec.get("summary"))
        if excluded is not None:
            self._excluded_by_summary += 1
            self._exclusion_reasons[excluded] += 1
            logger.debug(
                "[run %s] excluded %s (%s): %s",
                self.run_id, rec.get("requisition_number"), excluded, rec.get("summary"),
            )
            return False

        key = rec.get("requisition_number")
        if key and key in self._seen:
            return False  # the grid repeats rows across pages

        # No close-date window. SEPTA used to drop any quote closing sooner than
        # the shared MIN_DAYS_UNTIL_CLOSE (7 days), which silently withheld the
        # most urgent quotes in the grid. Every open quote is now kept and the
        # close date is exported as scraped, for the reader to judge.
        #
        # This is deliberately a SEPTA-only departure: app/core/closing_filter
        # still defines the rule and the other portals still apply it.
        record: dict[str, Any] = dict(rec)
        self._records.append(record)
        if key:
            self._seen[key] = record
        if len(self._preview) < PREVIEW_LIMIT:
            self._preview.append({**record, "documents": [], "error": None})
        return True

    def scrape_all_pages(self) -> int:
        """Page through the result grid, storing every new, non-excluded quote.

        Returns how many quotes were kept.
        """
        self.set_step("scraping_results")
        new_count = 0
        page_num = 1
        last_signature: list[str] | None = None

        while page_num <= MAX_PAGES:
            # An unfiltered run walks the whole grid, which can be hundreds of
            # pages — without a checkpoint inside the loop a stop request would
            # not take effect until the run had paged all the way to the end.
            self.raise_if_stopped()
            quotes = self._scrape_page()
            if not quotes:
                logger.info("[run %s] no quotes on page %s, stopping", self.run_id, page_num)
                break

            signature = sorted(
                str(q.get("requisition_number", "")) + str(q.get("summary", "")) for q in quotes
            )
            if last_signature is not None and signature == last_signature:
                logger.info("[run %s] duplicate page — end of pagination", self.run_id)
                break
            last_signature = signature

            for rec in quotes:
                if self._record_quote(rec):
                    new_count += 1

            total = len(self._records)
            run_manager.update_run(
                self.run_id, bids_found=total, bids_processed=total, bids=list(self._preview)
            )
            logger.info("[run %s] page %s scraped (run total %s)", self.run_id, page_num, total)

            if not self._click_next_page():
                break
            page_num += 1
            time.sleep(PAGE_CHANGE_SLEEP)

        if page_num > MAX_PAGES:
            run_manager.add_error(self.run_id, f"stopped at page cap ({MAX_PAGES})")
        return new_count

    # -- orchestration ------------------------------------------------------

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        self._save_run_row()
        try:
            self.start_driver()
            self.login()
            self.navigate_to_open_quotes()
            # One search: the optional date range, then Search, then the grid.
            self.apply_date_range()
            self.search()
            self.scrape_all_pages()

            # Say what the blacklist removed, and on which term. Reported even
            # when nothing matched, so "no exclusions" is a stated result rather
            # than an absent line that could equally mean the filter never ran.
            run_manager.update_run(
                self.run_id,
                excluded_summary_terms=list(exclusions.EXCLUDED_SUMMARY_TERMS),
                bids_excluded_by_summary=self._excluded_by_summary,
                exclusion_reasons=dict(self._exclusion_reasons),
            )
            if self._excluded_by_summary:
                breakdown = ", ".join(
                    f"{term}: {count}"
                    for term, count in self._exclusion_reasons.most_common()
                )
                logger.info(
                    "[run %s] summary exclusions: skipped %s quote(s) — %s",
                    self.run_id, self._excluded_by_summary, breakdown,
                )
                run_manager.add_warning(
                    self.run_id,
                    f"{self._excluded_by_summary} quote(s) skipped by the summary "
                    f"blacklist ({breakdown})",
                )
            else:
                logger.info("[run %s] summary exclusions: none matched", self.run_id)

            # No close-date reporting: the filter is gone, so there is nothing
            # to reconcile. `min_days_until_close` is deliberately left unset on
            # the run — the UI keys its "closing soon" banner off that field, so
            # omitting it is what stops SEPTA claiming a filter it no longer
            # applies, while every other portal still sets and shows it.
            logger.info(
                "[run %s] no close-date filter — every open quote kept (%s)",
                self.run_id, len(self._records),
            )

            if not self._records:
                run_manager.update_run(self.run_id, no_results=True)

            # Persist every scraped quote in one transaction (mirrors North
            # Dakota). Best-effort: a DB failure must not fail the run — the Excel
            # is then written straight from the in-memory records.
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
            if db_ok:
                # No Excel is written to disk any more — the sheet is rebuilt
                # from the DB on demand (Download button / completion email).
                run_manager.update_run(self.run_id, excel_exported=True)
            else:
                # DB outage: the quotes exist only in memory, so a disk Excel is
                # the only copy the download/email can serve. Named by the run's
                # search criteria, with a counter so identical same-day searches
                # never overwrite each other.
                name = sanitize_filename(
                    f"Septa_({self.dates.summary()})", max_length=150
                )
                candidate = self.run_dir / f"{name}.xlsx"
                counter = 2
                while candidate.exists():
                    candidate = self.run_dir / f"{name} ({counter}).xlsx"
                    counter += 1
                self.excel_path = candidate
                try:
                    export.generate_excel_from_records(self._records, self.excel_path)
                    run_manager.update_run(self.run_id, excel_path=str(self.excel_path), excel_exported=True)
                except Exception:  # noqa: BLE001 — never fail the run over the Excel
                    logger.exception("[run %s] Excel generation failed", self.run_id)
                    run_manager.add_error(self.run_id, "excel generation failed (see logs)")

            # Persist the sheet into the archive and delete the workspace. SEPTA
            # is an EXCEL_ONLY portal — its whole deliverable is the spreadsheet,
            # so this writes a bare .xlsx rather than wrapping it in a ZIP.
            self.set_step("packaging_results")
            archive_run(self.run_id)

            run_manager.update_run(self.run_id, status="completed", step="done")
            # Email/S3 notification on successful completion.
            notify_scrape_completion(self.run_id, "septa", len(self._records))
        except Exception as exc:  # noqa: BLE001 — a failed run must be reported, not crash the worker
            logger.exception("[run %s] failed", self.run_id)
            self.screenshot("fatal")
            run_manager.add_error(self.run_id, self.describe_failure(exc))
            run_manager.update_run(self.run_id, status="failed", step="failed")
        finally:
            self.cleanup()
            run_manager.update_run(self.run_id, finished_at=datetime.now().isoformat())
            self._save_run_row()
            run_manager.remove_empty_folder(self.run_id)

    def _save_run_row(self) -> None:
        run = run_manager.get_run(self.run_id)
        if not run:
            return
        try:
            export.save_run(run)
        except Exception:  # noqa: BLE001
            logger.exception("[run %s] save_run failed", self.run_id)


def execute_run(run_id: str, date_from: str | None = None, date_to: str | None = None) -> None:
    """Run one SEPTA scrape. Both dates are optional; omitting them searches
    every open quote."""
    SeptaScraper(run_id, OpenDateRange(start=date_from, end=date_to)).run()
