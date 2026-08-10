"""Selenium automation for the SEPTA vendor procurement portal.

One run, one module — **the caller picks which**:

    login
    module == quotes?    -> go to the Open Quotes search form
    module == open_bids? -> go to the Bid module's Open Bids form
                            (configured URL, else the menu link)
    "Opens from" date given?  -> fill it     <- entirely optional
                     not given -> type nothing
    click Search
    page through that grid, skipping blacklisted titles/summaries
    store what's left in the DB, export the workbook

**Strictly one.** The selected module is navigated to and searched; the other
is not opened at all. An earlier version ran both grids back to back, which
made every run pay for both and produced output the reader had to separate;
the module is now a run parameter (see `filters`) with Open Quotes as the
default, so an existing caller that sends no module gets what it always got.

The two grids describe different things — Open Quotes is a parts-requisition
feed, Open Bids the actual solicitations — so they stay apart end to end: their
own tables (`septa_bids` / `septa_open_bids`), their own worksheets, their own
counters. What they share is everything the requirement asks be consistent
across both: the login and session, the optional opens-from date, the
blacklist (see `exclusions` — one list, matched against a quote's summary and
a bid's title alike), and the export path.

The workbook always carries a worksheet for each module, whichever ran. The
unselected one is present and empty, so a sheet's absence never has to be
interpreted — every SEPTA workbook has the same shape.

**Keyword and commodity-code searching is gone.** The portal's Open Quotes grid
is a parts-requisition feed, and searching it term by term (a niche's keywords,
then its NIGP codes) returned a small, unreliable slice of it — the checklist's
keywords legitimately match nothing here, and its commodity codes were never
verified. Fetching the grid whole and filtering it locally is both simpler and
more complete, so the niche catalog, the per-term search loop, and the
re-navigation machinery that loop needed are all removed.

The "Opens from" date is **optional and has no default**. The previous scraper
substituted today's date whenever a run carried no other filter, which quietly
narrowed an unfiltered run to a single day. No date now means no date typing
at all, which is what returns every open quote.

Only the *from* side of the portal's Open Date Range is ever filled. The form
also has an "opens to" box and a closes pair; the run leaves them alone, so the
filter is an open-ended lower bound.

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
from app.core.base_scraper import BaseScraper, StopRequested
from app.core.filenames import sanitize_filename
from app.scrapers.septa import exclusions, export
from app.scrapers.septa.filters import (
    DEFAULT_MODULE,
    MODULE_LABELS,
    OPEN_BIDS,
    QUOTES,
    BadDate,
    OpenDateFilter,
    normalize_module,
)

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
# Per-candidate wait while crawling the menu for the Open Bids link. Much
# shorter than SEARCH_FORM_WAIT because a wrong candidate has no search form to
# render at all, so the full wait would be spent in full on each bad guess.
MENU_PROBE_WAIT = 6
# How many menu candidates to try. The link xpath ends with a deliberately
# broad clause (any anchor naming a bid), so on a rich menu it can match more
# anchors than are worth a page load each.
MAX_BIDS_MENU_CANDIDATES = 6

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

# XPath 1.0 has no lower-case(), so case-insensitive text matching goes through
# translate(). Bound once here rather than spelled out at each use.
_LOWER = "translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')"

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
    # The Open Date Range "from" box — the only filter input a run ever fills.
    # The form also has an "opens to" box and a closes pair; they are
    # deliberately left alone. Tolerant of an id change, but not required: a
    # run with no date never looks for it.
    "open_date_from_xpath": (
        "//*[@id='ctl00_ctl00_masterMain_cntMain_ctl00_txtOpensStartDate'] | "
        "//input[contains(@name, 'txtOpensStartDate')] | "
        "//input[contains(@id, 'OpenDate') or contains(@name, 'OpenDate') or "
        "contains(@id, 'FromDate') or contains(@name, 'FromDate') or "
        "contains(@id, 'StartDate') or contains(@name, 'StartDate')] | "
        "//input[contains(@class, 'date') and not(contains(@id, 'Close')) "
        "and not(contains(@id, 'End'))]"
    ),
    "search_btn_xpath": (
        "//a[contains(text(), 'Search') or contains(text(), 'SEARCH')] | "
        "//a[contains(@id, 'Search') or contains(@id, 'btnSearch')] | "
        "//button[contains(text(), 'Search') or contains(text(), 'SEARCH')] | "
        "//input[@value='Search' or @value='SEARCH' or @type='submit'] | "
        "//*[@id='searchButton'] | //*[contains(@class, 'search-btn')]"
    ),
    # -- Bid module ---------------------------------------------------------
    # The menu link to Open Bids, used when the configured URL does not land on
    # the Bid module's search form. Ordered most- to least-specific: the exact
    # wording first, then any anchor whose href is under a bids path, then any
    # remaining anchor naming a bid — with requisition/quote hrefs excluded so
    # the crawl can never walk back into the Quotes module it started from.
    "bids_link_xpath": (
        f"//a[contains({_LOWER}, 'open bid')] | "
        f"//a[contains({_LOWER}, 'search open bid')] | "
        "//a[contains(@href, '/bids/') or contains(@href, '/bid/')] | "
        f"//a[contains({_LOWER}, 'bid') and not(contains(@href, 'requisition')) "
        f"and not(contains({_LOWER}, 'quote'))]"
    ),
    # Confirms a page really is the Bid module and not the Quotes form the
    # portal redirected back to. Both forms carry a Search button, so the button
    # alone cannot tell them apart — see `_on_bids_form`.
    "bids_heading_xpath": (
        f"//h1[contains({_LOWER}, 'bid')] | //h2[contains({_LOWER}, 'bid')] | "
        f"//h3[contains({_LOWER}, 'bid')] | "
        f"//*[contains(@class, 'page-title') and contains({_LOWER}, 'bid')] | "
        f"//legend[contains({_LOWER}, 'bid')]"
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


# -- Open Bids column mapping ------------------------------------------------
#
# The Bid module's grid leads with Commodity Codes, so its fields do not sit
# where Open Quotes' do. Columns are matched by header text rather than counted
# from the left, which is what keeps a portal-side column change from silently
# shifting every value one place over.

# Headers that never hold data this stores. Commodity Codes is the first column
# of the Open Bids grid and is skipped outright.
_BID_IGNORED_HEADERS: tuple[str, ...] = ("commodity",)

# field -> header fragments that identify it, most specific first. Matched
# case-insensitively against the header's collapsed text.
_BID_HEADER_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bid_number", ("bid number", "bid #", "bid no", "solicitation number", "bid id")),
    ("title", ("title", "description", "summary")),
    ("open_date", ("open date", "opening date", "date opened", "opens")),
    ("close_date", ("close date", "closing date", "due date", "date closed", "closes")),
)


def _header_texts(row) -> list[str]:
    """The header row's cell texts. `th`s when present, else `td`s."""
    try:
        cells = row.find_elements(By.TAG_NAME, "th") or row.find_elements(By.TAG_NAME, "td")
        return [" ".join((cell.text or "").split()) for cell in cells]
    except WebDriverException:
        return []


def _bid_columns(headers: list[str]) -> dict[str, int]:
    """Map Open Bids fields to column indexes, from the grid's own headers.

    Returns `{}` when the headers do not identify at least a bid number and a
    title — a partial match is worse than none, because it would place two
    fields correctly and leave the rest to be guessed at.
    """
    columns: dict[str, int] = {}
    for index, raw in enumerate(headers):
        text = " ".join((raw or "").split()).lower()
        if not text or any(token in text for token in _BID_IGNORED_HEADERS):
            continue
        for field, patterns in _BID_HEADER_PATTERNS:
            if field in columns:
                continue
            if any(pattern in text for pattern in patterns):
                columns[field] = index
                break
    if "bid_number" not in columns or "title" not in columns:
        return {}
    return columns


def _at(texts: list[str], index: int) -> str:
    return texts[index] if index < len(texts) else ""


class SeptaScraper(BaseScraper):
    def __init__(
        self,
        run_id: str,
        dates: OpenDateFilter | None = None,
        module: str = DEFAULT_MODULE,
    ):
        super().__init__(run_id)
        # Which of the portal's two modules this run searches. Exactly one, and
        # it decides the whole flow: where the run navigates, which grid it
        # pages, which table and worksheet the rows land in.
        self.module = normalize_module(module)
        # The run's only search filter, and it is optional: an empty range means
        # the date boxes are never touched and the search returns every open row
        # in the selected module. There is no keyword, commodity code or niche
        # any more.
        self.dates = dates or OpenDateFilter()
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

        # -- Open Bids (the Bid module), scraped after the quotes ------------
        # Kept in their own lists and counters rather than merged into the
        # quotes ones: the two grids key on different columns and land in
        # different tables and sheets, so a shared total would describe neither.
        self._open_bids: list[dict[str, Any]] = []
        self._seen_open_bids: dict[str, dict[str, Any]] = {}
        # Title-blacklist tallies, counted apart from the quotes' for the same
        # reason — "12 skipped" is only actionable when it says which grid.
        self._excluded_by_title = 0
        self._title_exclusion_reasons: Counter[str] = Counter()
        # False until the Bid module is actually reached. A run that could not
        # get there must not report "0 open bids" as if it had looked.
        self._open_bids_reached = False

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

    # -- the Bid module -----------------------------------------------------

    def _on_bids_form(self, timeout: int = SEARCH_FORM_WAIT) -> bool:
        """True when the current page is the Bid module's search form.

        The Search button alone cannot decide this: the Quotes form has one too,
        so a configured Bids URL that 404s into a redirect back to Quotes would
        otherwise pass, and the Quotes grid would be scraped a second time and
        written to the Bids sheet. The button therefore only establishes that
        *some* search form rendered; the module is then identified from the URL,
        or from a heading when the URL is unhelpful.
        """
        if self._find(By.XPATH, SEL["search_btn_xpath"], timeout) is None:
            return False
        url = (self.driver.current_url or "").lower()
        if "requisition" in url or "quote" in url:
            return False
        if "bid" in url:
            return True
        try:
            return bool(self.driver.find_elements(By.XPATH, SEL["bids_heading_xpath"]))
        except WebDriverException:
            return False

    def _open_bids_via_menu(self) -> bool:
        """Find and click the Open Bids link, as a fallback for a wrong URL.

        The Bid module sits alongside Quotes in the vendor menu, so the link is
        on the page the run is already looking at. Every candidate is tried
        rather than only the first: the most specific xpath can match a heading
        anchor or a disabled item, and giving up on it would lose the module.
        """
        try:
            candidates = len(self.driver.find_elements(By.XPATH, SEL["bids_link_xpath"]))
        except WebDriverException:
            return False
        if not candidates:
            return False

        home = self.driver.current_url
        for index in range(min(candidates, MAX_BIDS_MENU_CANDIDATES)):
            # Re-find every time: the previous attempt navigated, so any element
            # captured before it is stale. Positional rather than by href
            # because the menu is server-rendered and stable within a session.
            try:
                links = self.driver.find_elements(By.XPATH, SEL["bids_link_xpath"])
                if index >= len(links):
                    break
                link = links[index]
                href = (link.get_attribute("href") or "").strip()
            except WebDriverException:
                continue

            try:
                # A plain href is followed directly; a __doPostBack anchor —
                # which is how this ASP.NET portal wires much of its
                # navigation, including the login submit — only works when
                # clicked, so both routes are supported.
                if href and not href.lower().startswith("javascript"):
                    self.driver.get(href)
                elif not self._safe_click(link):
                    continue
            except WebDriverException:
                continue

            # Short wait while probing: a candidate that is not the Bid module
            # has no search form to render, so the full timeout would be spent
            # in full on every wrong guess.
            if self._on_bids_form(MENU_PROBE_WAIT):
                logger.info("[run %s] reached Open Bids via the menu link %s",
                            self.run_id, href or "(postback)")
                return True

            # Wrong link — go back so the next candidate is looked for on the
            # menu page rather than wherever this one led.
            try:
                self.driver.get(home)
            except WebDriverException:
                break
        return False

    def navigate_to_open_bids(self) -> bool:
        """Load the Open Bids search form. True when it was reached.

        Two routes, in order: the configured URL, then the menu link. The URL is
        a setting rather than a constant because it is the one part of this flow
        that cannot be verified from the code — a portal that moves the module
        is then a .env change, not a release. Returns False rather than raising:
        the quotes are already scraped by this point, and losing the Bid module
        should cost the run that grid, not its whole output.
        """
        self.set_step("opening_open_bids")
        url = (settings.septa_bids_search_url or "").strip()
        if url:
            try:
                self.driver.get(url)
                if self._on_bids_form():
                    logger.info("[run %s] reached the Open Bids search form at %s",
                                self.run_id, url)
                    return True
                logger.warning(
                    "[run %s] %s did not land on the Open Bids search form — "
                    "looking for the menu link instead", self.run_id, url,
                )
            except WebDriverException as exc:
                logger.warning(
                    "[run %s] could not load %s (%s) — looking for the menu link instead",
                    self.run_id, url, exc.__class__.__name__,
                )

        # The menu link lives on the portal's own pages, so make sure we are on
        # one before crawling for it — a failed .get above can leave the browser
        # on an error page with no menu at all.
        try:
            if "septa" not in (self.driver.current_url or "").lower():
                self.driver.get(settings.septa_search_url)
        except WebDriverException:
            pass

        if self._open_bids_via_menu():
            return True

        self.screenshot("open_bids_not_found")
        return False

    # -- optional opens-from date + search ----------------------------------

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

    def apply_date_filter(self, module: str = "open quotes") -> None:
        """Fill the "Opens from" box — or deliberately do nothing.

        Nothing is the normal case. When the run carries no date the box is
        never located or typed into, and Search then returns every row in the
        grid. A date that will not parse is reported and skipped rather than
        replaced with a guess, so the run widens to everything instead of
        silently searching a day nobody asked for.

        The form's "opens to" box is never touched: the filter is an open-ended
        lower bound, and an upper bound could only hide rows.

        Both modules go through this: the Bid module's form carries the same
        Open Date Range, and a run applies its one optional date to each. So
        "no date given" means the Bids search is submitted with its filter
        fields untouched, which is what returns every open bid.
        """
        if self.dates.is_empty:
            logger.info(
                "[run %s] no opens-from date given — searching all %s",
                self.run_id, module,
            )
            return

        self.set_step("applying_date_filter")
        try:
            value = self.dates.portal_value()
        except BadDate as exc:
            logger.warning("[run %s] %s — searching unfiltered instead", self.run_id, exc)
            run_manager.add_warning(
                self.run_id,
                f"could not read the opens-from date {exc.value!r} (expected "
                "YYYY-MM-DD) — searched all open quotes instead",
            )
            return

        label = f"{module} Open Date Range 'from'"
        if self._fill_date(SEL["open_date_from_xpath"], value, label):
            logger.info("[run %s] %s = %s", self.run_id, label, value)
        else:
            run_manager.add_warning(self.run_id, f"could not enter the {label} ({value})")

    def search(self, module: str = "Open Quotes") -> None:
        """Click Search and wait for the results grid.

        Reached with the date box either filled or deliberately untouched — this
        does not care which, which is what makes "no date given" a plain
        unfiltered search rather than a separate code path.
        """
        self.set_step("searching")
        search_btn = self._find(By.XPATH, SEL["search_btn_xpath"], DATE_FIELD_WAIT)
        if not search_btn:
            self.screenshot("no_search_button")
            raise WebDriverException(f"SEPTA: could not find the {module} search button.")
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

    def _bid_extractor(self, headers: list[str]):
        """A row reader for the Open Bids grid, bound to this page's columns.

        The Bid module does **not** render the same shape as Open Quotes. Its
        first column is Commodity Codes, which is not stored, and the fields we
        want follow it — so reading cells 0..3 positionally took the commodity
        codes as the bid number and shifted every other field one place left.
        That put a bid *title* into `open_date`, a `varchar(64)`, and the
        oversized value aborted the whole insert: a 19-bid run stored nothing.

        Columns are therefore located by their headers, which survives the
        portal adding, moving or renaming one. The positional fallback below is
        only for a grid whose header row cannot be read.
        """
        columns = _bid_columns(headers)
        if columns:
            logger.info(
                "[run %s] open bids columns: %s",
                self.run_id,
                ", ".join(f"{field}={headers[i]!r}" for field, i in sorted(
                    columns.items(), key=lambda kv: kv[1]
                )),
            )
        else:
            # Logged with the actual header text so the real layout is visible
            # in the run's log rather than having to be guessed at again.
            logger.warning(
                "[run %s] could not match the open bids headers (%s) — falling "
                "back to position, skipping the first column",
                self.run_id, headers,
            )

        def extract(row) -> dict[str, str] | None:
            return self._extract_bid_row(row, columns)

        return extract

    def _extract_bid_row(self, row, columns: dict[str, int] | None = None) -> dict[str, str] | None:
        """One row of the Open Bids grid, read through `columns` when known."""
        try:
            cells = row.find_elements(By.TAG_NAME, "td")
            texts = [cell.text.strip() for cell in cells]
        except WebDriverException:
            return None

        if not texts:
            # Fallback for a grid that renders without <td>s.
            texts = [p.strip() for p in row.text.strip().split("\n") if p.strip()]
            if not texts:
                return None

        if columns:
            record = {
                field: (texts[index] if index < len(texts) else "")
                for field, index in columns.items()
            }
        else:
            # Positional fallback. Index 0 is the Commodity Codes column, which
            # is deliberately skipped — it is not data this stores.
            record = {
                "bid_number": _at(texts, 1),
                "title": _at(texts, 2),
                "open_date": _at(texts, 3),
                "close_date": _at(texts, 4),
            }

        for field in ("bid_number", "title", "open_date", "close_date"):
            record.setdefault(field, "")
        if not record["bid_number"]:
            record["bid_number"] = "N/A"
        return record

    def _scrape_page(self, make_extractor=None) -> list[dict[str, str]]:
        table = self._find_data_table()
        if not table:
            logger.warning("[run %s] no data table on this page", self.run_id)
            return []
        rows = table.find_elements(By.TAG_NAME, "tr")
        if len(rows) <= 1:
            return []
        # The header row is what the Open Bids reader maps its columns from, so
        # it is read here and handed to the extractor factory.
        extract = (make_extractor or (lambda _h: self._extract_row))(_header_texts(rows[0]))
        out: list[dict[str, str]] = []
        for row in rows[1:]:
            rec = extract(row)
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

    def _record_open_bid(self, rec: dict[str, str]) -> bool:
        """Add one scraped Open Bids row to the run. True when new and kept.

        The Open Bids counterpart of `_record_quote`, and the single gate every
        bid passes through on its way to the DB and the sheet — so it is where
        the **title** blacklist is applied. A blacklisted bid is skipped before
        anything else happens to it: its details are not read, nothing is
        downloaded for it, and it reaches neither the database nor the export.
        """
        # The blacklist runs first, before the dedup slot, for the same reason
        # it does for quotes: "skipped" and "already seen" must count different
        # things. Terms and matching are shared with the quotes grid — see
        # `exclusions`, which owns both.
        excluded = exclusions.excluded_by(rec.get("title"))
        if excluded is not None:
            self._excluded_by_title += 1
            self._title_exclusion_reasons[excluded] += 1
            logger.debug(
                "[run %s] excluded bid %s (%s): %s",
                self.run_id, rec.get("bid_number"), excluded, rec.get("title"),
            )
            return False

        key = rec.get("bid_number")
        if key and key in self._seen_open_bids:
            return False  # the grid repeats rows across pages

        record: dict[str, Any] = dict(rec)
        self._open_bids.append(record)
        if key:
            self._seen_open_bids[key] = record
        # Mirrored to the live table like quotes are. Without this a run against
        # the Bid module shows an empty results table for its whole duration —
        # which, now that a user can select only that module, is the entire run.
        if len(self._preview) < PREVIEW_LIMIT:
            self._preview.append({**record, "documents": [], "error": None})
        return True

    def _page_through(self, make_extractor, record, key_fields, on_page, what: str) -> int:
        """Walk a result grid page by page, recording every new row.

        Shared by both modules. The two grids differ only in how a row is read,
        where it is stored and what it is called, so the paging itself — the
        stop checkpoint, the repeated-page detection that ends pagination, and
        the page cap — is written once and cannot drift between them.

        `make_extractor` is a factory taking the page's header texts and
        returning the row reader — Open Bids maps its columns from those
        headers, and the headers are re-read per page so a layout that changes
        between pages cannot go unnoticed.

        Returns how many rows were kept.
        """
        new_count = 0
        page_num = 1
        last_signature: list[str] | None = None

        while page_num <= MAX_PAGES:
            # An unfiltered run walks the whole grid, which can be hundreds of
            # pages — without a checkpoint inside the loop a stop request would
            # not take effect until the run had paged all the way to the end.
            self.raise_if_stopped()
            rows = self._scrape_page(make_extractor)
            if not rows:
                logger.info("[run %s] no %s on page %s, stopping",
                            self.run_id, what, page_num)
                break

            signature = sorted(
                "".join(str(row.get(field, "")) for field in key_fields) for row in rows
            )
            if last_signature is not None and signature == last_signature:
                logger.info("[run %s] duplicate page — end of %s pagination",
                            self.run_id, what)
                break
            last_signature = signature

            for row in rows:
                if record(row):
                    new_count += 1

            total = on_page()
            logger.info("[run %s] %s page %s scraped (run total %s)",
                        self.run_id, what, page_num, total)

            if not self._click_next_page():
                break
            page_num += 1
            time.sleep(PAGE_CHANGE_SLEEP)

        if page_num > MAX_PAGES:
            run_manager.add_error(
                self.run_id, f"stopped at page cap ({MAX_PAGES}) while scraping {what}"
            )
        return new_count

    def scrape_all_pages(self) -> int:
        """Page through the Open Quotes grid, storing every new, kept quote.

        Returns how many quotes were kept.
        """
        self.set_step("scraping_results")

        def on_page() -> int:
            total = len(self._records)
            run_manager.update_run(
                self.run_id, bids_found=total, bids_processed=total, bids=list(self._preview)
            )
            return total

        # Open Quotes reads by position and always has — its layout is known and
        # has not moved — so its factory ignores the headers.
        return self._page_through(
            lambda _headers: self._extract_row, self._record_quote,
            ("requisition_number", "summary"), on_page, "quotes",
        )

    def scrape_open_bid_pages(self) -> int:
        """Page through the Open Bids grid, storing every new, kept bid.

        Returns how many bids were kept.
        """
        self.set_step("scraping_open_bids")

        def on_page() -> int:
            total = len(self._open_bids)
            # `bids_found`/`bids_processed` are the UI's generic result counters,
            # so the selected module feeds them whichever one it is;
            # `open_bids_found` additionally records the grid this came from.
            run_manager.update_run(
                self.run_id, bids_found=total, bids_processed=total,
                open_bids_found=total, bids=list(self._preview),
            )
            return total

        return self._page_through(
            self._bid_extractor, self._record_open_bid,
            ("bid_number", "title"), on_page, "open bids",
        )

    def scrape_open_bids(self) -> None:
        """The Bid module pass: navigate, optionally filter, search, scrape.

        Raises when the module cannot be reached. It is the whole run when it is
        the selected module, so failing to get there has to fail the run rather
        than complete with an empty sheet — "0 open bids" and "we never opened
        the Bid module" must not look the same to whoever reads the output.
        """
        if not self.navigate_to_open_bids():
            self.screenshot("open_bids_unreachable")
            raise WebDriverException(
                "SEPTA: could not reach the Open Bids search form — "
                f"{settings.septa_bids_search_url} did not land on it and no menu "
                "link matched. Set SEPTA_BIDS_SEARCH_URL in server/.env if the "
                "portal has moved the Bid module."
            )

        self._open_bids_reached = True
        # Same optional date, same rule as the Quotes form: given, it is typed
        # into the Bid module's Open Date Range; absent, the filter fields are
        # left untouched and Search returns every open bid.
        self.apply_date_filter("open bids")
        self.search("Open Bids")
        self.scrape_open_bid_pages()

    # -- reporting ----------------------------------------------------------

    @property
    def _noun(self) -> str:
        """What one row of the selected module is called, for log lines."""
        return "bid" if self.module == OPEN_BIDS else "quote"

    @property
    def _kept_count(self) -> int:
        """Rows the selected module contributed — the run's real result count."""
        return len(self._open_bids) if self.module == OPEN_BIDS else len(self._records)

    def _report_exclusions(self) -> None:
        """Say what the blacklist removed from the module that ran, and why.

        Reported even when nothing matched, so "no exclusions" is a stated
        result rather than an absent line that could equally mean the filter
        never ran. Only the selected module is described — narrating the other
        one's zero would read as a grid that was searched and came back empty.
        """
        bids = self.module == OPEN_BIDS
        skipped = self._excluded_by_title if bids else self._excluded_by_summary
        reasons = self._title_exclusion_reasons if bids else self._exclusion_reasons
        # The field the blacklist was matched against, and the noun for a row.
        field = "title" if bids else "summary"
        noun = f"open {self._noun}" if bids else self._noun

        run_manager.update_run(
            self.run_id,
            module=self.module,
            excluded_summary_terms=list(exclusions.EXCLUDED_SUMMARY_TERMS),
            # Both fields are always written so a consumer never has to guess
            # whether a missing one means zero or means "not this module".
            bids_excluded_by_summary=self._excluded_by_summary,
            exclusion_reasons=dict(self._exclusion_reasons),
            open_bids_found=len(self._open_bids),
            open_bids_excluded_by_title=self._excluded_by_title,
            title_exclusion_reasons=dict(self._title_exclusion_reasons),
        )

        if skipped:
            breakdown = ", ".join(
                f"{term}: {count}" for term, count in reasons.most_common()
            )
            logger.info(
                "[run %s] %s exclusions: skipped %s %s(s) — %s",
                self.run_id, field, skipped, noun, breakdown,
            )
            run_manager.add_warning(
                self.run_id,
                f"{skipped} {noun}(s) skipped by the {field} blacklist ({breakdown})",
            )
        else:
            logger.info("[run %s] %s exclusions: none matched", self.run_id, field)

        logger.info(
            "[run %s] scraped %s %s(s) from %s",
            self.run_id, self._kept_count, noun, MODULE_LABELS[self.module],
        )

    # -- orchestration ------------------------------------------------------

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        self._save_run_row()
        try:
            self.start_driver()
            self.login()
            # Strictly the selected module. The other one is never navigated
            # to, never searched and never paged — the whole point of making it
            # a choice rather than running both.
            logger.info(
                "[run %s] module: %s (%s)",
                self.run_id, self.module, MODULE_LABELS[self.module],
            )
            if self.module == OPEN_BIDS:
                self.scrape_open_bids()
            else:
                self.navigate_to_open_quotes()
                self.apply_date_filter()
                self.search()
                self.scrape_all_pages()

            self._report_exclusions()

            # No close-date reporting: the filter is gone, so there is nothing
            # to reconcile. `min_days_until_close` is deliberately left unset on
            # the run — the UI keys its "closing soon" banner off that field, so
            # omitting it is what stops SEPTA claiming a filter it no longer
            # applies, while every other portal still sets and shows it.
            logger.info(
                "[run %s] no close-date filter — every open %s kept (%s)",
                self.run_id, self._noun, self._kept_count,
            )

            if not self._records and not self._open_bids:
                run_manager.update_run(self.run_id, no_results=True)

            # Persist every scraped row in one transaction per grid (mirrors
            # North Dakota). Best-effort: a DB failure must not fail the run —
            # the Excel is then written straight from the in-memory records.
            # Separate transactions so one grid failing to store still leaves
            # the other in the database.
            run = run_manager.get_run(self.run_id) or {"run_id": self.run_id}
            db_ok = True
            try:
                stored = export.save_bids(run, self._records)
                run_manager.update_run(self.run_id, bids_stored_in_db=stored)
            except Exception:  # noqa: BLE001 — DB issues shouldn't abort the run
                db_ok = False
                logger.exception("[run %s] DB save failed", self.run_id)
                run_manager.add_error(self.run_id, "db save failed (see logs)")
            try:
                stored_bids = export.save_open_bids(run, self._open_bids)
                run_manager.update_run(self.run_id, open_bids_stored_in_db=stored_bids)
            except Exception:  # noqa: BLE001
                db_ok = False
                logger.exception("[run %s] open-bids DB save failed", self.run_id)
                run_manager.add_error(self.run_id, "open-bids db save failed (see logs)")

            # Recorded on the run, not just logged: the export layer has to know
            # the database is not the source of truth for this run, or it
            # regenerates an empty workbook over the fallback sheet below and
            # the run is delivered with nothing in it.
            run_manager.update_run(self.run_id, db_save_failed=not db_ok)

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
                # The name carries the module as well as the date, so two runs
                # of the same date against different modules do not collide.
                name = sanitize_filename(
                    f"Septa_({self.dates.summary(self.module)})", max_length=150
                )
                candidate = self.run_dir / f"{name}.xlsx"
                counter = 2
                while candidate.exists():
                    candidate = self.run_dir / f"{name} ({counter}).xlsx"
                    counter += 1
                self.excel_path = candidate
                try:
                    export.generate_excel_from_records(
                        self._records, self.excel_path, self._open_bids
                    )
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
            # Email/S3 notification on successful completion, counting the
            # module that ran — which is everything the workbook holds.
            notify_scrape_completion(self.run_id, "septa", self._kept_count)
        except StopRequested:
            # The user pressed Stop. run_manager has already locked the run to
            # "stopped" and is suppressing later status/error writes, so there
            # is nothing to record — but this must not fall through to the
            # handler below, which would log a traceback under "failed" and try
            # to screenshot a browser that stopping has already closed. A run
            # the user ended is not a run that broke.
            logger.info("[run %s] stopped by user", self.run_id)
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
    run_id: str, date_from: str | None = None, module: str = DEFAULT_MODULE
) -> None:
    """Run one SEPTA scrape against one module.

    `module` is "quotes" or "open_bids", defaulting to quotes so a caller that
    predates the choice gets the run it always got. The opens-from date is
    optional; omitting it searches every open row in the selected module.
    """
    SeptaScraper(run_id, OpenDateFilter(opens_from=date_from), module).run()
