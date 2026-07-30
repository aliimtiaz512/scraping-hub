"""Selenium automation for the SEPTA vendor procurement portal.

Flow: open the vendor login page -> sign in -> navigate to "Open Quotes" ->
apply an optional date filter (defaults to today) -> page through the whole
results grid, storing every row (requisition number, summary, open/close dates)
in the DB -> generate an Excel from the DB into the run folder.

The selectors and navigation heuristics are ported verbatim from the SEPTA
integration package (`septa_hub_package/`) so the portal's behaviour is
preserved; only the plumbing is adapted to the hub's BaseScraper / run_manager /
SQLAlchemy conventions so storage matches every other portal.
"""

import logging
import time
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
from app.core.closing_filter import MIN_DAYS_UNTIL_CLOSE, days_until_close
from app.core.exports import archive_run
from app.services.notifier import notify_scrape_completion
from app.core.base_scraper import BaseScraper, StopRequested
from app.core.filenames import sanitize_filename
from app.scrapers.septa import export, niches

logger = logging.getLogger(__name__)

# -- timeouts (seconds) ------------------------------------------------------
LOGIN_FIELD_WAIT = 30       # per-field wait for the login form to render
LOGIN_SETTLE_SLEEP = 2      # let ASP.NET/Bootstrap finish wiring the form
LOGIN_REDIRECT_WAIT = 45    # postback + redirect can be slow
NAVIGATION_SLEEP = 3
SEARCH_FORM_WAIT = 20       # for the search form's keyword box to render
SEARCH_RESULT_WAIT = 20
DATE_FIELD_WAIT = 5
NEXT_PAGE_WAIT = 10
PAGE_CHANGE_SLEEP = 2

MAX_PAGES = 50
PREVIEW_LIMIT = 100   # rows mirrored to the live run state for the UI table

# The >=7-days-until-close rule and its date parsing live in one shared place
# (app/core/closing_filter) so every portal behaves identically.

# -- navigation heuristics (ported from the package config) ------------------
OPEN_QUOTES_LINK_TEXTS = [
    "View Open Quotes", "eProcurement", "Quotations",
    "Quote Module", "Direct Quote Requests",
]
OPEN_QUOTES_HREF_PATTERNS = ["openquote", "OpenQuote"]
MENU_PROCUREMENT_KEYWORDS = ["procurement", "quote", "bid", "tender"]

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
    "date_input_xpath": (
        "//*[@id='ctl00_ctl00_masterMain_cntMain_ctl00_txtOpensStartDate'] | "
        "//input[contains(@name, 'txtOpensStartDate')] | "
        "//input[contains(@id, 'OpenDate') or contains(@name, 'OpenDate') or "
        "contains(@id, 'FromDate') or contains(@name, 'FromDate') or "
        "contains(@id, 'StartDate') or contains(@name, 'StartDate')] | "
        "//input[contains(@class, 'date') and not(contains(@id, 'Close')) "
        "and not(contains(@id, 'End'))]"
    ),
    # Keyword Search box on the Open Quotes page.
    "keyword_xpath": (
        "//*[@id='ctl00_ctl00_masterMain_cntMain_ctl00_txtKeyword'] | "
        "//input[contains(@name, 'txtKeyword')] | //input[contains(@id, 'txtKeyword')]"
    ),
    # Commodity Code box on the Open Quotes page.
    "commodity_xpath": (
        "//*[@id='ctl00_ctl00_masterMain_cntMain_ctl00_txtCommodityCode'] | "
        "//input[contains(@name, 'txtCommodityCode')] | //input[contains(@id, 'txtCommodityCode')]"
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
    def __init__(
        self,
        run_id: str,
        date_filter: str | None = None,
        keyword: str | None = None,
        commodity_code: str | None = None,
        niche: str | None = None,
    ):
        super().__init__(run_id)
        self.date_filter = (date_filter or "").strip() or None
        self.keyword = (keyword or "").strip() or None
        self.commodity_code = (commodity_code or "").strip() or None
        # When set, the run searches every keyword and commodity code this niche
        # owns instead of the single keyword/commodity above.
        self.niche = (niche or "").strip() or None
        self.niche_label: str | None = None
        self.excel_path: Path | None = None
        # Full in-memory copy of every scraped row — the Excel fallback source if
        # the DB is unavailable.
        self._records: list[dict[str, Any]] = []
        # requisition number -> its record in _records. Run-level (not per-page)
        # so a quote surfaced by several of a niche's terms is stored once, with
        # every term that found it recorded on it.
        self._seen: dict[str, dict[str, Any]] = {}
        # Rows mirrored to the live run state for the UI table.
        self._preview: list[dict[str, Any]] = []
        # Close-date filter tallies (see MIN_DAYS_UNTIL_CLOSE): quotes dropped for
        # closing too soon, and quotes kept despite an unreadable close date.
        self._skipped_closing_soon = 0
        self._kept_unreadable_close = 0

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
        self.set_step("opening_open_quotes")

        # The direct URL is the reliable route — the link-text/href heuristics
        # below never matched this portal's actual "Search Open Quotes" anchor.
        # They stay as a fallback in case the URL ever moves.
        if self._open_search_form():
            logger.info("[run %s] reached Open Quotes search form", self.run_id)
            return
        logger.warning("[run %s] search form URL didn't work — trying link navigation",
                       self.run_id)

        attempts = []
        for text in OPEN_QUOTES_LINK_TEXTS:
            attempts.append(lambda t=text: self._click_by_xpath(f"//a[contains(text(), '{t}')]"))
            attempts.append(lambda t=text: self._click_by_text(t))
        for pattern in OPEN_QUOTES_HREF_PATTERNS:
            attempts.append(lambda p=pattern: self._click_by_xpath(f"//a[contains(@href, '{p}')]"))
        attempts.append(lambda: self._click_by_xpath("//button[contains(text(), 'Open Quotes')]"))
        attempts.append(self._explore_menu_structure)

        for attempt in attempts:
            try:
                if attempt():
                    time.sleep(NAVIGATION_SLEEP)
                    if self._is_on_open_quotes_page():
                        logger.info("[run %s] reached Open Quotes", self.run_id)
                        return
            except WebDriverException as exc:
                logger.debug("[run %s] nav attempt failed: %s", self.run_id, exc)
                continue

        self.screenshot("open_quotes_not_found")
        raise WebDriverException("Could not navigate to the SEPTA Open Quotes page.")

    def _click_by_xpath(self, xpath: str) -> bool:
        try:
            el = self.driver.find_element(By.XPATH, xpath)
            if el.is_displayed() and el.is_enabled():
                return self._safe_click(el)
        except WebDriverException:
            pass
        return False

    def _click_by_text(self, text: str) -> bool:
        try:
            el = self.driver.find_element(By.XPATH, f"//*[contains(text(), '{text}')]")
            if el.is_displayed() and el.is_enabled():
                return self._safe_click(el)
        except WebDriverException:
            pass
        return False

    def _explore_menu_structure(self) -> bool:
        try:
            menus = self.driver.find_elements(
                By.CSS_SELECTOR, "nav, .navbar, .menu, .sidebar, ul.menu"
            )
            for menu in menus:
                try:
                    for link in menu.find_elements(By.TAG_NAME, "a"):
                        if any(kw in link.text.lower() for kw in MENU_PROCUREMENT_KEYWORDS):
                            if link.is_displayed() and link.is_enabled() and self._safe_click(link):
                                time.sleep(NAVIGATION_SLEEP)
                                return True
                except WebDriverException:
                    continue
        except WebDriverException:
            pass
        return False

    def _is_on_open_quotes_page(self) -> bool:
        """True only when the *filter form* is actually present.

        Deliberately not a page-text check: the results list says "Open Quotes"
        too (it links back with "Search Open Quotes"), so matching on wording
        reported success while sitting on a page with no inputs to type into.
        """
        try:
            if self._visible_field(SEL["keyword_xpath"]) is not None:
                return True
        except WebDriverException:
            pass
        return False

    # -- date filter + search ----------------------------------------------

    def _resolve_date(self, force_today_when_empty: bool) -> str | None:
        """The open-date value to type, in the portal's MM/DD/YYYY form.

        An explicit date always wins. Otherwise today is used only when the run
        has no other filter at all — a niche run must not be silently narrowed
        to today, or every one of its searches would return almost nothing.
        """
        if self.date_filter:
            try:
                return datetime.strptime(self.date_filter, "%Y-%m-%d").strftime("%m/%d/%Y")
            except ValueError:
                logger.warning("[run %s] bad date %r; defaulting to today", self.run_id, self.date_filter)
                run_manager.add_warning(self.run_id, f"could not parse date '{self.date_filter}'; used today")
                return datetime.now().strftime("%m/%d/%Y")
        return datetime.now().strftime("%m/%d/%Y") if force_today_when_empty else None

    def _visible_field(self, xpath: str):
        """The first visible input matching `xpath`, freshly located.

        Always re-locate before touching a filter box: each search is an ASP.NET
        postback that re-renders the panel, so any element reference held from
        before the postback is stale.
        """
        for field in self.driver.find_elements(By.XPATH, xpath):
            try:
                if field.is_displayed():
                    return field
            except WebDriverException:
                continue
        return None

    def _open_search_form(self) -> bool:
        """Load the Open Quotes search form and wait for its keyword box.

        Straight to the known URL rather than hunting for a link: a search sends
        the browser to /vendor/requisitions/list/, which has no filter inputs and
        no link the old text patterns matched ("Search Open Quotes", not "View
        Open Quotes"), so link-hunting silently left us on the results page and
        every term after the first failed to type.
        """
        try:
            self.driver.get(settings.septa_search_url)
        except WebDriverException:
            logger.warning("[run %s] could not load the search form URL", self.run_id, exc_info=True)
            return False
        return self._find(By.XPATH, SEL["keyword_xpath"], SEARCH_FORM_WAIT) is not None

    def _ensure_filter_form(self) -> None:
        """Make sure the Open Quotes filter form is usable before a search.

        Each search leaves the browser on the results list, which carries no
        filter inputs — so this reloads the form for all but the first term.
        """
        if self._visible_field(SEL["keyword_xpath"]) is not None:
            return
        if not self._open_search_form():
            raise WebDriverException(
                "SEPTA: could not get back to the Open Quotes search form "
                f"({settings.septa_search_url})."
            )

    def _fill_filter(self, xpath: str, value: str, label: str) -> bool:
        """Set a filter box to `value`, verifying the value actually landed.

        A bare clear+send_keys silently no-ops when the input has just been
        re-rendered by a search postback — the same way the login password box
        does, which is what `_fill_field` (verify, then force the value through
        the DOM and fire input/change) was written to solve. Route the filter
        boxes through it too, and on failure re-navigate to a clean Open Quotes
        page and try once more, so one bad postback costs a retry rather than
        the term.
        """
        for attempt in (1, 2):
            field = self._visible_field(xpath)
            if field is not None and self._fill_field(field, value):
                return True
            if attempt == 1:
                logger.warning(
                    "[run %s] %s did not take (attempt 1) — reloading the search form and retrying",
                    self.run_id, label,
                )
                self._open_search_form()
        return False

    def _clear_filters(self) -> None:
        """Blank the keyword and commodity boxes between searches.

        Without this the next search inherits the previous term (the portal keeps
        filled inputs across a postback) and every result after the first would
        be filtered by an unintended keyword.
        """
        for xpath in (SEL["keyword_xpath"], SEL["commodity_xpath"]):
            field = self._visible_field(xpath)
            if field is None:
                continue
            try:
                field.clear()
                # A postback-stale box can ignore clear() exactly as it ignores
                # send_keys; force it empty so no term leaks into the next search.
                if (field.get_attribute("value") or ""):
                    self._fill_field(field, "")
            except WebDriverException:
                continue

    def _run_search(
        self,
        keyword: str | None = None,
        commodity_code: str | None = None,
        date_target: str | None = None,
    ) -> None:
        """Fill the Open Quotes filters and hit Search.

        One call = one search. Only the arguments given are typed; everything
        else is cleared first, so searches never contaminate each other.
        """
        self._ensure_filter_form()
        self._clear_filters()

        if keyword and not self._fill_filter(SEL["keyword_xpath"], keyword, f"keyword '{keyword}'"):
            run_manager.add_warning(self.run_id, f"could not enter keyword '{keyword}'")
            raise WebDriverException(f"SEPTA: could not type keyword '{keyword}' into the search box.")
        if commodity_code and not self._fill_filter(
            SEL["commodity_xpath"], commodity_code, f"commodity code '{commodity_code}'"
        ):
            run_manager.add_warning(self.run_id, f"could not enter commodity code '{commodity_code}'")
            raise WebDriverException(
                f"SEPTA: could not type commodity code '{commodity_code}' into the search box."
            )
        # Date last: a re-navigation inside the retries above would have wiped it.
        if date_target is not None and not self._set_date_field(date_target):
            run_manager.add_warning(self.run_id, f"could not enter open date '{date_target}'")

        search_btn = self._find(By.XPATH, SEL["search_btn_xpath"], DATE_FIELD_WAIT)
        if not search_btn:
            raise WebDriverException("SEPTA: could not find the Open Quotes search button.")
        self._safe_click(search_btn)

        try:
            WebDriverWait(self.driver, SEARCH_RESULT_WAIT).until(
                EC.presence_of_element_located((By.XPATH, SEL["data_table_wait_xpath"]))
            )
        except TimeoutException:
            logger.warning("[run %s] timeout waiting for the results table", self.run_id)

    def apply_filters(self) -> None:
        """Single-search mode: the run's own date/keyword/commodity, one search.

        This is the pre-niche behaviour, kept for ad-hoc API runs that pass a
        bare keyword or commodity code instead of a niche.
        """
        self.set_step("applying_filters")
        any_filter = bool(self.date_filter or self.keyword or self.commodity_code)
        date_target = self._resolve_date(force_today_when_empty=not any_filter)

        self.set_step("searching")
        self._run_search(
            keyword=self.keyword,
            commodity_code=self.commodity_code,
            date_target=date_target,
        )
        self.scrape_all_pages()

    def run_niche_searches(self) -> None:
        """Niche mode: one search per keyword, then one per commodity code.

        Terms are never concatenated — the portal returns far more for a single
        term than for a combined query, and this matches how BidNet already
        searches its keyword catalog. Results from every search accumulate into
        one deduplicated set (see `_record_quotes`), so a requisition found by
        several terms appears once with all of them listed.

        A term that fails is logged and skipped: one bad search must not cost
        the run its other nineteen.
        """
        terms = niches.niche_terms(self.niche)
        if terms is None:
            raise WebDriverException(
                f"SEPTA niche '{self.niche}' is not in the catalog — check "
                "server/app/scrapers/septa/niches.py."
            )
        self.niche_label, keywords, codes = terms
        if not keywords and not codes:
            raise WebDriverException(
                f"SEPTA niche '{self.niche_label}' has no keywords or commodity codes "
                "configured — nothing to search."
            )

        # A niche run is already narrow; only narrow it by date if the user
        # explicitly asked for one.
        date_target = self._resolve_date(force_today_when_empty=False)

        searches: list[tuple[str, str]] = (
            [("keyword", term) for term in keywords] + [("commodity", code) for code in codes]
        )
        total = len(searches)
        logger.info(
            "[run %s] niche %r: %s keyword(s) + %s code(s) = %s searches",
            self.run_id, self.niche_label, len(keywords), len(codes), total,
        )
        run_manager.update_run(
            self.run_id,
            niche=self.niche,
            niche_label=self.niche_label,
            searches_total=total,
        )

        for index, (kind, term) in enumerate(searches, start=1):
            self.raise_if_stopped()
            self.set_step(f"searching {kind} {index}/{total}: {term}")
            try:
                self._run_search(
                    keyword=term if kind == "keyword" else None,
                    commodity_code=term if kind == "commodity" else None,
                    date_target=date_target,
                )
                found = self.scrape_all_pages(matched_term=term)
            except StopRequested:
                raise
            except (TimeoutException, WebDriverException) as exc:
                logger.warning("[run %s] search failed for %s %r: %s",
                               self.run_id, kind, term, exc.__class__.__name__)
                run_manager.add_error(self.run_id, f"search failed for {kind} '{term}'")
                self.screenshot(f"search_{term}")
                continue

            logger.info(
                "[run %s] [%s/%s] %s %r -> %s new (run total %s)",
                self.run_id, index, total, kind, term, found, len(self._records),
            )
            run_manager.update_run(self.run_id, searches_done=index)

    def _set_date_field(self, target: str) -> bool:
        """Fill the open-date field, falling back to any visible date-ish input.

        Uses the same verified fill as the keyword/commodity boxes — this input
        sits on the same postback-rendered panel and swallows send_keys the same
        way.
        """
        field = self._visible_field(SEL["date_input_xpath"])
        if field is not None and self._fill_field(field, target):
            return True

        for inp in self.driver.find_elements(By.TAG_NAME, "input"):
            try:
                if not (inp.is_displayed() and inp.get_attribute("type") in ("text", "date")):
                    continue
                id_ = (inp.get_attribute("id") or "").lower()
                name_ = (inp.get_attribute("name") or "").lower()
                if ("date" in id_ or "date" in name_) and self._fill_field(inp, target):
                    return True
            except WebDriverException:
                continue
        return False

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

    def _record_quote(self, rec: dict[str, str], matched_term: str | None) -> bool:
        """Add one scraped row to the run, or merge it into what's already there.

        Returns True only when the quote is new to the run. Deduplication is
        run-level, so the same requisition surfacing under several of a niche's
        terms is stored once — `matched_terms` then lists every term that found
        it, which is what the Excel's "Matched Term(s)" column shows.
        """
        key = rec.get("requisition_number")
        existing = self._seen.get(key) if key else None
        if existing is not None:
            if matched_term and matched_term not in existing["_terms"]:
                existing["_terms"].append(matched_term)
                existing["matched_terms"] = ", ".join(existing["_terms"])
            return False

        # Keep only quotes with at least MIN_DAYS_UNTIL_CLOSE days left. An
        # unreadable close date can't be proven to fail, so it's kept (and
        # tallied); a date we can read that's too near is dropped.
        days_left = days_until_close(rec.get("close_date"))
        if days_left is None:
            self._kept_unreadable_close += 1
        elif days_left < MIN_DAYS_UNTIL_CLOSE:
            self._skipped_closing_soon += 1
            return False

        record: dict[str, Any] = {
            **rec,
            "niche": self.niche_label or self.niche,
            "matched_terms": matched_term or "",
            # Working list behind matched_terms; stripped before persistence.
            "_terms": [matched_term] if matched_term else [],
        }
        self._records.append(record)
        if key:
            self._seen[key] = record
        if len(self._preview) < PREVIEW_LIMIT:
            self._preview.append({**record, "documents": [], "error": None})
        return True

    def scrape_all_pages(self, matched_term: str | None = None) -> int:
        """Page through the current result grid, storing every new quote.

        `matched_term` is the keyword or commodity code whose search produced
        this grid, recorded on each quote as provenance. Returns how many quotes
        were new to the run — searches after the first often return mostly
        repeats, and that count is what makes the log readable.
        """
        self.set_step("scraping_results")
        new_count = 0
        page_num = 1
        last_signature: list[str] | None = None

        while page_num <= MAX_PAGES:
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
                if self._record_quote(rec, matched_term):
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
            if self.niche:
                # Niche mode: one search per keyword and per commodity code,
                # merged into a single deduplicated set.
                self.run_niche_searches()
            else:
                # Ad-hoc mode: the run's own single keyword/commodity/date.
                self.apply_filters()

            # The working provenance list is an implementation detail of the
            # dedup — drop it before the records reach the DB or the Excel.
            for record in self._records:
                record.pop("_terms", None)

            # Surface the close-date filter's effect so the smaller count is never
            # a mystery: how many quotes were dropped for closing too soon, how
            # many were kept despite an unreadable close date, and the threshold.
            run_manager.update_run(
                self.run_id,
                min_days_until_close=MIN_DAYS_UNTIL_CLOSE,
                bids_skipped_closing_soon=self._skipped_closing_soon,
                bids_kept_unreadable_close=self._kept_unreadable_close,
            )
            logger.info(
                "[run %s] close-date filter (≥%sd): kept %s, skipped %s closing soon, %s unreadable kept",
                self.run_id, MIN_DAYS_UNTIL_CLOSE, len(self._records),
                self._skipped_closing_soon, self._kept_unreadable_close,
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
                criteria = ", ".join(
                    part for part in (
                        f"niche={self.niche_label or self.niche}" if self.niche else "",
                        f"keyword={self.keyword}" if self.keyword else "",
                        f"daterange={self.date_filter}" if self.date_filter else "",
                        f"commoditycodes={self.commodity_code}" if self.commodity_code else "",
                    ) if part
                ) or "today's open quotes"
                name = sanitize_filename(f"Septa_({criteria})", max_length=150)
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


def execute_run(
    run_id: str,
    date_filter: str | None = None,
    keyword: str | None = None,
    commodity_code: str | None = None,
    niche: str | None = None,
) -> None:
    SeptaScraper(run_id, date_filter, keyword, commodity_code, niche).run()
