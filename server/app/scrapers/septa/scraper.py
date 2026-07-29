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
from app.core.base_scraper import BaseScraper
from app.core.filenames import sanitize_filename
from app.scrapers.septa import export

logger = logging.getLogger(__name__)

# -- timeouts (seconds) ------------------------------------------------------
LOGIN_FIELD_WAIT = 30       # per-field wait for the login form to render
LOGIN_SETTLE_SLEEP = 2      # let ASP.NET/Bootstrap finish wiring the form
LOGIN_REDIRECT_WAIT = 45    # postback + redirect can be slow
NAVIGATION_SLEEP = 3
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
OPEN_QUOTES_PAGE_KEYWORDS = ["open quote", "open quotes"]
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
    "open_quotes_search_xpath": "//button[contains(text(), 'Search')]",
    "open_quotes_date_placeholder_xpath": "//input[contains(@placeholder, 'Date')]",
}


class SeptaScraper(BaseScraper):
    def __init__(
        self,
        run_id: str,
        date_filter: str | None = None,
        keyword: str | None = None,
        commodity_code: str | None = None,
    ):
        super().__init__(run_id)
        self.date_filter = (date_filter or "").strip() or None
        self.keyword = (keyword or "").strip() or None
        self.commodity_code = (commodity_code or "").strip() or None
        self.excel_path: Path | None = None
        # Full in-memory copy of every scraped row — the Excel fallback source if
        # the DB is unavailable.
        self._records: list[dict[str, Any]] = []
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
        self.screenshot("login_locked")
        if "lock" in detail.lower():
            raise WebDriverException(
                f"SEPTA account is locked ({when}) — wait for the lockout to expire "
                f"before running again; retrying now restarts the 60-minute window. "
                f"Portal said: {detail}"
            )
        # "N more attempts" — a warning, not yet a lock. Surface it loudly.
        logger.warning("[run %s] SEPTA lockout warning (%s): %s",
                       self.run_id, when, detail)
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
        try:
            page_text = self.driver.page_source.lower()
            if any(kw in page_text for kw in OPEN_QUOTES_PAGE_KEYWORDS):
                return True
            if self.driver.find_elements(By.XPATH, SEL["open_quotes_search_xpath"]):
                return True
            if self.driver.find_elements(By.XPATH, SEL["open_quotes_date_placeholder_xpath"]):
                return True
        except WebDriverException:
            pass
        return False

    # -- date filter + search ----------------------------------------------

    def apply_filters(self) -> None:
        """Apply whichever of the three Open Quotes filters the user supplied —
        open date, keyword, commodity code — then search.

        All three are optional and freely combinable. When none is provided the
        date defaults to today, preserving the original date-only behaviour; when
        a keyword and/or commodity is provided without a date, no date is forced
        so the search isn't silently narrowed to today.
        """
        self.set_step("applying_filters")
        any_filter = bool(self.date_filter or self.keyword or self.commodity_code)

        # -- open date (explicit, or today's default when nothing else is set) --
        target: str | None = None
        if self.date_filter:
            try:
                target = datetime.strptime(self.date_filter, "%Y-%m-%d").strftime("%m/%d/%Y")
            except ValueError:
                logger.warning("[run %s] bad date %r; defaulting to today", self.run_id, self.date_filter)
                run_manager.add_warning(self.run_id, f"could not parse date '{self.date_filter}'; used today")
                target = datetime.now().strftime("%m/%d/%Y")
        elif not any_filter:
            target = datetime.now().strftime("%m/%d/%Y")

        if target is not None:
            logger.info("[run %s] applying date filter %s", self.run_id, target)
            self._set_date_field(target)

        # -- keyword + commodity code -------------------------------------------
        if self.keyword:
            logger.info("[run %s] applying keyword filter %r", self.run_id, self.keyword)
            if not self._set_text_field(SEL["keyword_xpath"], self.keyword):
                run_manager.add_warning(self.run_id, f"could not enter keyword '{self.keyword}'")
        if self.commodity_code:
            logger.info("[run %s] applying commodity code filter %r", self.run_id, self.commodity_code)
            if not self._set_text_field(SEL["commodity_xpath"], self.commodity_code):
                run_manager.add_warning(self.run_id, f"could not enter commodity code '{self.commodity_code}'")

        self.set_step("searching")
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

    def _set_date_field(self, target: str) -> None:
        """Fill the open-date field, falling back to any visible date-ish input."""
        for field in self.driver.find_elements(By.XPATH, SEL["date_input_xpath"]):
            try:
                if field.is_displayed():
                    field.clear()
                    field.send_keys(target)
                    return
            except WebDriverException:
                continue
        for inp in self.driver.find_elements(By.TAG_NAME, "input"):
            try:
                if inp.is_displayed() and inp.get_attribute("type") in ("text", "date"):
                    id_ = (inp.get_attribute("id") or "").lower()
                    name_ = (inp.get_attribute("name") or "").lower()
                    if "date" in id_ or "date" in name_:
                        inp.clear()
                        inp.send_keys(target)
                        return
            except WebDriverException:
                continue

    def _set_text_field(self, xpath: str, value: str) -> bool:
        """Type `value` into the first visible input matching `xpath`."""
        for field in self.driver.find_elements(By.XPATH, xpath):
            try:
                if field.is_displayed():
                    field.clear()
                    field.send_keys(value)
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

    def scrape_all_pages(self) -> None:
        self.set_step("scraping_results")
        preview: list[dict[str, Any]] = []
        seen: set[str] = set()
        scraped = 0
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
                key = rec.get("requisition_number")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)

                # Keep only quotes with at least MIN_DAYS_UNTIL_CLOSE days left.
                # An unreadable close date can't be proven to fail, so it's kept
                # (and tallied); a date we can read that's too near is dropped.
                days_left = days_until_close(rec.get("close_date"))
                if days_left is None:
                    self._kept_unreadable_close += 1
                elif days_left < MIN_DAYS_UNTIL_CLOSE:
                    self._skipped_closing_soon += 1
                    continue

                self._records.append(rec)
                scraped += 1
                if len(preview) < PREVIEW_LIMIT:
                    preview.append({**rec, "documents": [], "error": None})

            run_manager.update_run(
                self.run_id, bids_found=scraped, bids_processed=scraped, bids=list(preview)
            )
            logger.info("[run %s] page %s scraped (total %s)", self.run_id, page_num, scraped)

            if not self._click_next_page():
                break
            page_num += 1
            time.sleep(PAGE_CHANGE_SLEEP)

        if page_num > MAX_PAGES:
            run_manager.add_error(self.run_id, f"stopped at page cap ({MAX_PAGES})")

    # -- orchestration ------------------------------------------------------

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        self._save_run_row()
        try:
            self.start_driver()
            self.login()
            self.navigate_to_open_quotes()
            self.apply_filters()
            self.scrape_all_pages()

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

            # Package the run into one archive ZIP (cumulative Excel + any
            # files) and delete the workspace — nothing stays on local disk.
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
) -> None:
    SeptaScraper(run_id, date_filter, keyword, commodity_code).run()
