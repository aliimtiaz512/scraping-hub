"""Selenium automation for the MFMP vendor portal.

Flow: login -> Advertisements -> Advanced Search -> search criteria -> Search ->
Export Excel -> store in DB -> open each bid -> download documents.

A run searches one of two ways, decided by execute_run's arguments:
  commodity codes — a single search with every code selected.
  keywords        — one search per keyword, results merged and de-duplicated by
                    ad number so an ad several keywords match downloads once.

Either way the search is narrowed to a posting-date window when the run asked
for one — the same two fields, filled by the same code, for both modes and for
the sweep that subclasses this. See `apply_date_range`.

The portal is an Angular Material single-page app. Selectors below were verified
against the live site; the fiddly parts are the commodity-code control (a
ngx-mat-select-search multi-select whose options load asynchronously and which
stays disabled until they do), the CDK overlay backdrops that intercept clicks
until they finish animating out, and the datepicker inputs, which take a typed
date only through real keystrokes and only commit it on blur.
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
from app.core.base_scraper import BaseScraper, StopRequested
from app.core.filenames import sanitize_filename
from app.scrapers.myflorida import accounts, dates, storage
from app.scrapers.myflorida.ingest import ingest_excel
from app.scrapers.myflorida.workbook import merge_exports
from app.core.exports import archive_run
from app.services.notifier import notify_scrape_completion

logger = logging.getLogger(__name__)


class LoginTimeout(Exception):
    """The login page never loaded. Carries a readable message for the UI, since a
    raw Selenium stacktrace tells the operator nothing actionable."""


def describe_error(exc: Exception, step: str | None = None) -> str:
    """One readable line for the UI.

    Selenium's str() is a page of stacktrace whose first line is often just
    "Message:" with nothing after it, so fall back to the exception type and the
    step that was running — which is what actually tells you where it broke.
    """
    if isinstance(exc, LoginTimeout):
        return str(exc)
    # Read .msg rather than str(): Selenium's __str__ appends the stacktrace and
    # renders a missing message as the literal "Message: None".
    raw = (exc.msg or "") if isinstance(exc, WebDriverException) else str(exc)
    first_line = raw.split("\n")[0].removeprefix("Message:").strip()
    where = f" during {step}" if step else ""
    if not first_line:
        return f"{exc.__class__.__name__}{where}"
    return f"{first_line[:240]} ({exc.__class__.__name__}{where})"

# ---------------------------------------------------------------------------
# Selectors — verified against the live Angular Material portal.
# ---------------------------------------------------------------------------
ADS_URL = "https://vendor.myfloridamarketplace.com/vendor/ads"

SEL = {
    "login_email": (By.CSS_SELECTOR, "input[formcontrolname='username']"),
    "login_password": (By.CSS_SELECTOR, "input[formcontrolname='password']"),
    "login_submit": (By.CSS_SELECTOR, "button[type='submit']"),
    "advanced_search_button": (By.XPATH, "//button[contains(., 'Advanced Search')]"),
    # Present on the signed-in shell whatever route the OTP flow lands on, so a
    # session that came back somewhere other than the ads page is still
    # recognised as authenticated. See `_authenticated`.
    "dashboard_marker": (
        By.CSS_SELECTOR,
        "mat-toolbar a[href*='/vendor/ads'], mat-sidenav-container, .vendor-dashboard",
    ),
    "max_results_select": (By.XPATH, "//mat-form-field[.//mat-label[contains(.,'Maximum')]]//mat-select"),
    "ad_status_panel_header": (By.XPATH, "//mat-expansion-panel-header[.//mat-panel-title[contains(normalize-space(.),'Ad Status')]]"),
    "ad_status_options": (By.XPATH, "//mat-selection-list[@aria-label='Ad Status']//mat-list-option"),
    "ad_type_panel_header": (By.XPATH, "//mat-expansion-panel-header[.//mat-panel-title[contains(normalize-space(.),'Ad Type')]]"),
    "ad_type_options": (By.XPATH, "//mat-selection-list[@aria-label='Ad Type']//mat-list-option"),
    # The only free-text field on Advanced Search; capped at 100 chars by the portal.
    "title_input": (By.CSS_SELECTOR, "input[formcontrolname='title']"),
    # -- Posting Start/End Date -------------------------------------------
    # Two locators per field, tried in this order by `_date_input`.
    #
    # The formcontrolname is the stable half of these fields' outer HTML: it is
    # what the portal's own Angular code binds the datepicker to. Everything
    # around it is generated per render — `id="mat-input-1"`, the `ng-tns-c57-6`
    # style scope, `data-mat-calendar="mat-datepicker-0"` — and all of it
    # renumbers the moment another form field is added above, so none of it is
    # matched on. `.mat-datepicker-input` pins the match to the date control
    # rather than to any other input the same name might one day be given.
    #
    # The label XPath is the fallback for a build that renames the control. The
    # words "Start Date" and "End Date" are what a person reads off the form, so
    # they are the thing most likely to survive a rewrite of what is under them.
    # It is anchored on the `mat-form-field-flex` wrapper rather than on the
    # `<mat-form-field>` outside it: the flex div is the outermost element the
    # portal's markup for these fields was captured at, so it is the outermost
    # one we know for certain is there.
    "posting_start_date": (By.CSS_SELECTOR, "input[formcontrolname='openDate'].mat-datepicker-input"),
    "posting_start_date_by_label": (
        By.XPATH,
        "//div[contains(@class,'mat-form-field-flex')][.//mat-label[contains(normalize-space(.),'Start Date')]]"
        "//input[contains(@class,'mat-datepicker-input')]",
    ),
    "posting_end_date": (By.CSS_SELECTOR, "input[formcontrolname='endDate'].mat-datepicker-input"),
    "posting_end_date_by_label": (
        By.XPATH,
        "//div[contains(@class,'mat-form-field-flex')][.//mat-label[contains(normalize-space(.),'End Date')]]"
        "//input[contains(@class,'mat-datepicker-input')]",
    ),
    # Only used if neither locator above resolves: the date fields sit on the
    # Advanced Search form directly today, but the portal collapses other
    # criteria into expansion panels and would not warn us if it did the same
    # to these.
    "date_panel_header": (
        By.XPATH,
        "//mat-expansion-panel-header[.//mat-panel-title[contains(normalize-space(.),'Date')]]",
    ),
    "reset_button": (By.XPATH, "//button[normalize-space(.)='Reset']"),
    "commodity_panel_header": (By.XPATH, "//mat-expansion-panel-header[.//*[contains(text(),'Commodity')]]"),
    "commodity_select": (By.ID, "mat-select-commodity-code"),
    "overlay_search_input": (By.CSS_SELECTOR, ".cdk-overlay-container input.mat-select-search-input:not(.mat-select-search-hidden)"),
    "overlay_options": (By.CSS_SELECTOR, ".cdk-overlay-container mat-option"),
    "overlay_backdrop": (By.CSS_SELECTOR, ".cdk-overlay-backdrop-showing"),
    "search_button": (By.XPATH, "//button[normalize-space(.)='Search']"),
    "results_rows": (By.CSS_SELECTOR, "tbody tr"),
    # The async loading spinner shown while a search runs. The portal renders no
    # "no results" message and the results table exists before a search is even
    # submitted, so a spinner cycle (appear -> clear) is the only signal that the
    # search actually executed. See submit_search.
    "progress_spinner": (By.CSS_SELECTOR, "mat-progress-spinner, mat-spinner"),
    "document_links": (By.CSS_SELECTOR, "a.document-link"),
    "export_excel": (By.XPATH, "//button[contains(., 'Export')]"),
}

# The advanced-search results table columns, in order: Title, Number, Agency Ad
# Number, Version, Organization, Ad Type, Start Date, End Date. Title is cell 1;
# the clickable Number link is cell 2.
MAX_RESULTS = "100"  # portal offers 25/50/75/100

# The login form renders after the Angular bundle boots, which on a degraded
# network runs well past the default element wait.
LOGIN_FORM_TIMEOUT = 60

# Breathing room after the ads landing page reports ready, so its async summary
# cards finish reflowing before we click Advanced Search. The intercepts this
# prevents are a too-early click, not a too-short wait — hence a settle here
# rather than a bigger WAIT_TIMEOUT.
LANDING_SETTLE_SECONDS = 2

# The per-bid detail round trip: click the Number link, wait for the /detail/
# route, come back to the results grid.
#
# Above the default element wait because these are route changes on an Angular
# SPA that re-queries on the way back, not element lookups on a page that is
# already up — and the portal degrades under exactly the load a hundred-bid run
# puts on it, so the tail of a long run is where a bid is slowest, not the head.
# Not raised further than this, because the ceiling is paid twice: a bid that is
# genuinely unreachable now waits it out on both attempts, so 90s here is 3
# minutes of a run rather than the 1 minute it used to be. That is the right
# trade at one or two failures in a run of a hundred and the wrong one if the
# portal is down — which is what the attempt count in the error line is for.
BID_PAGE_TIMEOUT = 90

# How many times a bid's detail page is attempted before it is recorded as
# failed. A timeout here is usually the grid or the route being slow for a
# moment, not the bid being unreachable: the retry re-resolves the Number link
# from a restored results page, which is the state the first attempt expected to
# find and occasionally did not.
BID_ATTEMPTS = 2

# Breathing room after the detail route resolves, before its document list is
# read. The route change is not the render — the attachments arrive after it,
# and reading too early finds a bid with no documents rather than an error.
DETAIL_RENDER_SECONDS = 3

# How long to look for the Posting Start/End Date inputs. Short, unlike the
# global element wait: these are two fields on a form that has already rendered
# by the time we reach for them, so an absent one means the form changed shape,
# not that it is still loading — and a keyword run would otherwise pay the full
# wait twice per keyword to learn that.
DATE_FIELD_TIMEOUT = 10

# Politeness gap between keyword passes; the portal degrades under rapid repeated
# navigation and a keyword run reloads the heavy landing page once per keyword.
KEYWORD_PAUSE_SECONDS = 2

# Ad Status options, keyed by API value -> the list-option text shown in the portal.
AD_STATUS_LABELS = {
    "preview": "PREVIEW",
    "open": "OPEN",
    "closed": "CLOSED",
    "withdrawn": "WITHDRAWN",
}

# Ad Type options, keyed by API value -> the list-option text shown in the portal.
AD_TYPE_LABELS = {
    "agency_decision": "Agency Decision",
    "grant_opportunities": "Grant Opportunities",
    "informational_notice": "Informational Notice",
    "invitation_to_bid": "Invitation to Bid",
    "invitation_to_negotiate": "Invitation to Negotiate",
    "request_for_proposals": "Request for Proposals",
    "public_meeting_notice": "Public Meeting Notice",
    "request_for_information": "Request for Information",
    "request_for_statement_of_qualifications": "Request for Statement of Qualifications",
    "single_source": "Single Source",
}


class MFMPScraper(BaseScraper):
    """Scrapes MFMP in one of two modes, set by which list the caller passes.

    codes    -> a single Advanced Search with every commodity code selected.
    keywords -> one Advanced Search per keyword, results merged and de-duplicated.
    """

    def __init__(
        self,
        run_id: str,
        codes: list[str],
        ad_statuses: list[str] | None = None,
        ad_types: list[str] | None = None,
        keywords: list[str] | None = None,
        date_range: dates.PostingDateRange | None = None,
    ):
        super().__init__(run_id)
        self.codes = codes
        self.keywords = [k.strip() for k in (keywords or []) if k.strip()]
        # An empty list means: leave the portal's Ad Status list untouched (every status).
        self.ad_statuses = [s for s in (ad_statuses or []) if s in AD_STATUS_LABELS]
        # Likewise for Ad Type — empty means every type.
        self.ad_types = [t for t in (ad_types or []) if t in AD_TYPE_LABELS]
        # The posting-date window this run was launched with. Belongs to the
        # search form rather than to what is typed into it, which is why it is
        # one value for the run and not one per keyword. An unset window means
        # every posting date — what every run made before this existed got.
        self.date_range = date_range or dates.PostingDateRange()
        # Whether the "window did not go in" warning has already been recorded.
        # A keyword run makes one pass per keyword and a form that has changed
        # shape fails all of them; the run record wants that said once.
        self._date_failure_reported = False
        # And whether the portal's date inputs turned out not to be on the form
        # at all, so later passes skip looking for them again.
        self._date_fields_missing = False
        self.excel_path: Path | None = None
        # Resolved at the top of run() rather than here: a constructor that can
        # raise on a missing credential turns a misconfigured account into an
        # exception where the caller expects a scraper, instead of a run that
        # fails with a reason someone can act on.
        self.account: accounts.Account | None = None

    @property
    def keyword_mode(self) -> bool:
        return bool(self.keywords)

    def report_date_window(self) -> None:
        """Say, once per run, what the posting-date window is going to do.

        There is one reason this is a method and not a log line: on MyFlorida a
        filter that did not apply is **invisible**. The portal renders no "no
        results" message and its results table exists before a search is even
        submitted, so a search that was never narrowed looks exactly like one
        that was. A user who set a window and got everything back would have no
        way to tell, and the run record would quietly agree with them.

        So this runs before the browser does, and states the window on the run
        record itself. It is the optimistic half of the account: the pessimistic
        half is `apply_date_range`, which retracts it — warning and flipping
        `date_filter_applied` back to False — if the portal's own date fields
        turn out not to take the value. With `dates.PORTAL_DATE_FILTER_READY`
        off, nothing is typed at all and the retraction is issued here instead.
        """
        if not self.date_range.is_set:
            return
        summary = self.date_range.describe()
        if dates.PORTAL_DATE_FILTER_READY:
            logger.info("[run %s] [DATE WINDOW]: %s", self.run_id, summary)
            run_manager.update_run(self.run_id, date_filter_applied=True)
            return
        message = (
            f"The posting-date window ({summary}) was NOT applied: entering it into "
            f"MyFlorida's Posting Start/End Date fields is switched off for this "
            f"deployment. These results cover every posting date, not the window "
            f"requested."
        )
        logger.warning("[run %s] %s", self.run_id, message)
        run_manager.add_warning(self.run_id, message)
        run_manager.update_run(self.run_id, date_filter_applied=False)
        self._date_failure_reported = True

    # -- flow steps ---------------------------------------------------------

    def login(self, attempts: int = 3) -> None:
        """Log in, retrying a page that stalls on load.

        The login page intermittently takes longer than the renderer's patience
        (~60s) and driver.get() raises a timeout, even though a retry seconds later
        loads it fine. Retry rather than lose the whole run to a transient stall.
        """
        self.set_step("logging_in")
        email = None
        for attempt in range(1, attempts + 1):
            try:
                self.driver.get(settings.mfmp_login_url)
                # Retry the form wait too, not just the load: with page_load_strategy
                # "eager" get() returns at DOMContentLoaded, so the Angular app still
                # has to boot and render — on a slow network that outlasts the normal
                # element wait, and the whole sequence is worth another attempt.
                email = self.wait(LOGIN_FORM_TIMEOUT).until(EC.element_to_be_clickable(SEL["login_email"]))
                break
            except (TimeoutException, WebDriverException) as exc:
                if attempt == attempts:
                    raise LoginTimeout(
                        f"login page did not load after {attempts} attempts — the portal "
                        f"or network was unresponsive ({exc.__class__.__name__})"
                    ) from exc
                logger.warning("[run %s] login page stalled (attempt %d/%d), retrying",
                               self.run_id, attempt, attempts)
                run_manager.add_error(self.run_id, f"login page stalled (attempt {attempt}/{attempts}) — retrying")
                # A hung load leaves the tab mid-navigation; stop it before retrying.
                try:
                    self.driver.execute_script("window.stop();")
                except WebDriverException:
                    pass
        account = self.account or accounts.get(None)
        logger.info(" └── [AUTHENTICATION]: Injecting %s credentials into login form...",
                    account.label)
        email.clear()
        email.send_keys(account.username)
        password = self.driver.find_element(*SEL["login_password"])
        password.clear()
        password.send_keys(account.password)
        self.driver.find_element(*SEL["login_submit"]).click()
        self._await_authenticated()

    def _await_authenticated(self) -> None:
        """Wait out the one-time password, then confirm we are really inside.

        MFMP answers a correct email/password with a one-time code sent to the
        account's email or phone. There is nothing on this side that can produce
        that code, so the browser runs visible (see `run`) and this step waits
        for a person to type it into the open window — the same human-in-the-loop
        shape the North Dakota portal's CAPTCHA uses.

        Two things this must not do. It must not treat *leaving* `/login` as
        being logged in: the OTP challenge is its own route, so the old check
        passed the moment the code was demanded and handed a half-authenticated
        session to the search step, which then failed somewhere unrelated with a
        message about a missing button. And it must not sit on the default
        element wait, which is far shorter than a person takes to fetch a code
        from their phone. So it waits for the *dashboard* — the thing that only
        exists once the session is real — for as long as `mfmp_otp_wait_seconds`
        allows.
        """
        timeout = (
            settings.mfmp_otp_wait_seconds if settings.mfmp_manual_otp else LOGIN_FORM_TIMEOUT
        )
        if settings.mfmp_manual_otp:
            self.set_step("awaiting_otp")
            message = (
                f"MyFloridaMarketPlace sent a one-time password. Enter it in the open "
                f"Chrome window — the run continues by itself once you are signed in "
                f"(waiting up to {timeout}s)."
            )
            logger.info("[run %s] 👉 %s", self.run_id, message)
            # On the run, not just in the log: this is the one moment the run
            # needs a person, and nobody is reading the server's stdout.
            run_manager.add_warning(self.run_id, message)
            run_manager.update_run(
                self.run_id, awaiting_otp=True, otp_wait_seconds=timeout
            )

        try:
            self.wait(timeout).until(self._authenticated)
        except TimeoutException as exc:
            self.screenshot("otp_not_completed")
            raise LoginTimeout(
                f"MFMP sign-in did not complete within {timeout}s. "
                + (
                    "The one-time password was not entered in the open Chrome window "
                    "in time — start the run again and have the code ready, or raise "
                    "mfmp_otp_wait_seconds in server/.env."
                    if settings.mfmp_manual_otp
                    else "The account was challenged for a one-time password and "
                    "mfmp_manual_otp is off, so nothing could answer it."
                )
            ) from exc
        finally:
            run_manager.update_run(self.run_id, awaiting_otp=False)

        self.set_step("logged_in")

    def _authenticated(self, driver) -> bool:
        """True once the portal has landed us somewhere only a real session
        reaches. Checked as "the dashboard is on screen", not as "the URL no
        longer says login" — an OTP prompt satisfies the second and not the
        first, which is the whole difference this method exists for."""
        try:
            url = (driver.current_url or "").lower()
        except WebDriverException:
            return False
        if "/login" in url:
            return False
        # The ads landing page and the portal's post-login shell both carry the
        # Advanced Search button; either means we are through.
        return bool(
            driver.find_elements(*SEL["advanced_search_button"])
            or driver.find_elements(*SEL["dashboard_marker"])
        )

    def open_advertisements(self, attempts: int = 3) -> None:
        """Load the ads landing page and let it settle before anyone clicks it.

        Two portal behaviours make this fiddly. The landing page renders its
        "Advertisement Summary" chart and "Recommended Advertisements" cards
        asynchronously, and they reflow the page around the Advanced Search button
        after it first becomes clickable — click too early and the cards take it.
        Separately the page intermittently comes back blank under repeated rapid
        navigation (a keyword run loads it once per keyword), so a stalled render
        gets retried rather than losing the whole pass, as login() does.
        """
        self.set_step("opening_advertisements")
        for attempt in range(1, attempts + 1):
            try:
                self.driver.get(ADS_URL)
                button = self.wait().until(EC.element_to_be_clickable(SEL["advanced_search_button"]))
                self.scroll_into_view(button)
                self._wait_no_backdrop()
                time.sleep(LANDING_SETTLE_SECONDS)
                return
            except (TimeoutException, WebDriverException) as exc:
                if attempt == attempts:
                    raise
                logger.warning("[run %s] ads page stalled (attempt %d/%d), retrying",
                               self.run_id, attempt, attempts)
                run_manager.add_warning(
                    self.run_id,
                    f"ads page stalled (attempt {attempt}/{attempts}) — retrying "
                    f"({exc.__class__.__name__})",
                )
                # A hung load leaves the tab mid-navigation; stop it before retrying.
                try:
                    self.driver.execute_script("window.stop();")
                except WebDriverException:
                    pass

    def open_advanced_search(self) -> None:
        self.set_step("opening_advanced_search")
        self._robust_click(SEL["advanced_search_button"])
        self._set_max_results(MAX_RESULTS)
        if not self.keyword_mode:
            # Expand the Commodity Codes accordion so its multi-select renders.
            header = self.wait().until(EC.element_to_be_clickable(SEL["commodity_panel_header"]))
            if header.get_attribute("aria-expanded") != "true":
                self._robust_click(header)
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

    def _select_list_filter(self, name: str, header_key: str, options_key: str, labels: list[str]) -> None:
        """Toggle on each of `labels` in one of the advanced-search selection lists.

        These controls are expansion panels wrapping a mat-selection-list; options are
        inline mat-list-option items (no CDK overlay) and the list is multi-select, so
        each requested label is clicked. Callers skip this entirely when nothing is
        requested, leaving the list untouched so the portal returns every value.
        Best-effort — a failure here never fails the run.
        """
        wanted = {label.lower(): label for label in labels}
        try:
            # _set_max_results has just closed a mat-select overlay, whose backdrop
            # can still be animating out and would swallow the panel click.
            self._wait_no_backdrop()
            header = self.wait().until(EC.element_to_be_clickable(SEL[header_key]))
            if header.get_attribute("aria-expanded") != "true":
                self._robust_click(header)
            options = self.wait().until(EC.presence_of_all_elements_located(SEL[options_key]))
            matched = set()
            for option in options:
                # Read textContent rather than .text: the label lives in a nested
                # .mat-list-text div and Selenium's .text returns "" for options that
                # are below the fold or mid-animation, which would spuriously report
                # every option as "option not found".
                text = (option.get_attribute("textContent") or "").strip().lower()
                if text in wanted:
                    self._robust_click(option)
                    time.sleep(0.5)
                    matched.add(text)
            for missing in wanted.keys() - matched:
                run_manager.add_error(self.run_id, f"{name} {wanted[missing]}: option not found")
        except (TimeoutException, WebDriverException):
            run_manager.add_error(self.run_id, f"{name} ({', '.join(labels)}): not selectable")

    def select_ad_status(self) -> None:
        """Select one or more Ad Status filters (Preview/Open/Closed/Withdrawn)."""
        if not self.ad_statuses:
            return
        self.set_step("selecting_ad_status")
        self._select_list_filter(
            "ad status",
            "ad_status_panel_header",
            "ad_status_options",
            [AD_STATUS_LABELS[s] for s in self.ad_statuses],
        )

    def select_ad_type(self) -> None:
        """Select one or more Ad Type filters (Invitation to Bid, Single Source, ...)."""
        if not self.ad_types:
            return
        self.set_step("selecting_ad_type")
        self._select_list_filter(
            "ad type",
            "ad_type_panel_header",
            "ad_type_options",
            [AD_TYPE_LABELS[t] for t in self.ad_types],
        )

    # -- posting-date window ------------------------------------------------

    def apply_date_range(self) -> None:
        """Put this run's posting-date window into the portal's own date fields.

        Called once per search pass, immediately before Search, by all three
        modes — keyword, commodity code and the ad-status sweep. They are three
        ways of driving the same form, so the window is applied in the one place
        they share rather than three times over.

        Both fields are written on every pass, including when the run asked for
        no window: an empty value clears the field. That matters because the
        form is reached fresh each pass and a date left behind — the portal's
        own default, or the previous keyword's window on a form that did not
        reset — would narrow a search nobody asked to narrow, and would do it
        invisibly. "No window" has to mean an empty field, not an unread one.

        A field that will not take the value is not allowed to pass quietly.
        MyFlorida renders no "no results" message, so a search that was never
        narrowed looks exactly like one that was; a run whose window did not go
        in therefore says so on the run record and not only in a log, the same
        way it does when the injection is switched off outright.
        """
        if not dates.PORTAL_DATE_FILTER_READY:
            return
        if self._date_fields_missing:
            # Settled on an earlier pass. Re-proving it costs the element wait
            # four times over, and a keyword run would pay that per keyword.
            if self.date_range.is_set:
                self._report_date_window_failure()
            return
        if self.date_range.is_set:
            self.set_step("applying_date_window")
        # The Ad Status / Ad Type panels were just clicked and their overlay may
        # still be animating out over these inputs.
        self._wait_no_backdrop()
        # Both fields are written even when the first one fails, rather than
        # short-circuiting: an End Date left holding last month is its own wrong
        # search, and the log should name both fields that went wrong, not the
        # first one that did.
        start_ok = self._fill_date_field(
            "Start Date", "posting_start_date", self.date_range.portal_start
        )
        end_ok = self._fill_date_field(
            "End Date", "posting_end_date", self.date_range.portal_end
        )
        if start_ok and end_ok:
            if self.date_range.is_set and not self._date_failure_reported:
                logger.info(
                    " ├── [DATE WINDOW APPLIED]: %s to %s",
                    self.date_range.portal_start or "(any)",
                    self.date_range.portal_end or "(any)",
                )
                # Confirmed here as well as announced in `report_date_window`,
                # so the run record stands on what the fields actually took.
                # Not raised back to True once a pass has failed: a keyword run
                # whose window went in for eight keywords and not the ninth
                # covered every posting date for the ninth, and the record has
                # to keep saying so.
                run_manager.update_run(self.run_id, date_filter_applied=True)
            return
        # Only a window that was asked for can fail to be applied; clearing a
        # field the portal does not have is not a failure.
        if self.date_range.is_set:
            self._report_date_window_failure()

    def _fill_date_field(self, name: str, key: str, value: str) -> bool:
        """Clear one date input and type `value` into it; confirm it took.

        Returns False only when the field was found and would not hold the
        value. A field that is not on the form at all is reported through
        `_date_input` and returns False there, so the caller sees one answer:
        did the window go in.
        """
        element = self._date_input(name, key)
        if element is None:
            return False
        for attempt in range(1, 3):
            try:
                self._type_date(element, value)
                shown = element.get_attribute("value")
            except (TimeoutException, WebDriverException) as exc:
                logger.warning(
                    "[run %s] %s field: %s on attempt %d",
                    self.run_id, name, exc.__class__.__name__, attempt,
                )
                shown = None
            if dates.same_portal_date(shown, value):
                return True
            # Re-resolve before retrying: a datepicker that rejected the text
            # can re-render its input, leaving the handle we hold stale.
            element = self._date_input(name, key) or element
        logger.warning(
            "[run %s] %s field would not hold %r (shows %r)",
            self.run_id, name, value, shown,
        )
        return False

    def _date_input(self, name: str, key: str):
        """Resolve one of the two posting-date inputs, or None.

        Tries the Angular form-control name first and the visible label second —
        see the note above these selectors for why neither the element id nor
        the `ng-tns-*` scope class is used. If both miss, an expansion panel is
        opened on the chance the portal has moved the dates behind one, and the
        pair is tried once more.
        """
        found = self._find_date_input(key)
        if found is not None:
            return found
        if self._expand_date_panel():
            found = self._find_date_input(key)
            if found is not None:
                return found
        # Remembered for the rest of the run: a form missing these inputs on one
        # pass is missing them on the next, and the lookups are not free.
        self._date_fields_missing = True
        if self.date_range.is_set:
            logger.warning(
                "[run %s] MyFlorida's %s field is not on the Advanced Search form — "
                "the portal's search form has changed and this run's posting-date "
                "window could not be entered.",
                self.run_id, name,
            )
        return None

    def _find_date_input(self, key: str):
        for locator in (SEL[key], SEL[f"{key}_by_label"]):
            try:
                element = self.wait(DATE_FIELD_TIMEOUT).until(
                    EC.presence_of_element_located(locator)
                )
            except (TimeoutException, WebDriverException):
                continue
            if element.is_enabled():
                return element
        return None

    def _expand_date_panel(self) -> bool:
        """Best-effort: open an expansion panel whose title mentions a date.
        True only if one was there and closed, so the caller knows a retry is
        worth making."""
        try:
            header = self.driver.find_element(*SEL["date_panel_header"])
            if header.get_attribute("aria-expanded") == "true":
                return False
            self._robust_click(header)
            return True
        except (TimeoutException, WebDriverException):
            return False

    def _type_date(self, element, value: str) -> None:
        """Empty the field, then type `value` as keystrokes.

        Select-all-and-delete rather than `element.clear()`: Angular binds to the
        input event, and `clear()` empties the DOM value without firing one — it
        leaves the form control still holding the old date behind a field that
        looks empty, which is the worst of both. Real keystrokes go through the
        same path a person's do, so the datepicker parses them the same way.

        The trailing Tab is what commits the parse. Without a blur the typed
        text can still be sitting unparsed in the input when Search reads the
        form, and the search runs on the previous value.
        """
        self.scroll_into_view(element)
        self._wait_no_backdrop()
        element.click()
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.DELETE)
        if value:
            element.send_keys(value)
        element.send_keys(Keys.TAB)
        # The datepicker writes its parsed value back on blur; give Angular's
        # change detection the tick it needs before the value is read back.
        time.sleep(0.5)
        self._wait_no_backdrop()

    def _report_date_window_failure(self) -> None:
        """Say on the run itself that the window did not go in.

        Once per run, not once per pass: a keyword run makes one pass per
        keyword and a form that has changed shape will fail every one of them,
        and forty copies of the same warning would bury the rest of the run's
        record rather than emphasise this one.
        """
        if self._date_failure_reported:
            return
        self._date_failure_reported = True
        message = (
            f"The posting-date window ({self.date_range.describe()}) was NOT applied: "
            f"MyFlorida's Posting Start/End Date fields would not take it. These "
            f"results cover every posting date, not the window requested."
        )
        logger.warning("[run %s] %s", self.run_id, message)
        run_manager.add_warning(self.run_id, message)
        run_manager.update_run(self.run_id, date_filter_applied=False)
        self.screenshot("date_window_not_applied")

    def enter_keyword(self, keyword: str) -> None:
        """Search on `keyword` via the Title field — one keyword per search.

        Title is Advanced Search's only free-text input, so a keyword matches
        against ad titles alone; an ad whose title is generic but whose body is on
        topic will not be found. Only one keyword fits per search (the field is a
        single input, not a term list), which is why a keyword run makes one pass
        per keyword rather than one pass total.
        """
        self.set_step(f"entering_keyword:{keyword}")
        field = self.wait().until(EC.element_to_be_clickable(SEL["title_input"]))
        self.scroll_into_view(field)
        field.clear()
        field.send_keys(keyword[:100])  # the portal caps this input at 100 chars

    # Search outcomes returned by submit_search.
    RESULTS = "results"            # rows came back
    EMPTY = "empty"                # spinner cycled (search ran) but zero rows
    EMPTY_UNCONFIRMED = "empty_unconfirmed"  # no spinner seen and zero rows

    def submit_search(self) -> str:
        """Run the search and report what came back.

        The portal renders no "no results" message, and the results table exists
        before a search is submitted, so a zero-result search is DOM-identical to
        one that never ran. We therefore key off the loading spinner: it appears
        while the query runs and clears when it finishes. Counting *displayed*
        spinners against a pre-click baseline tolerates any always-on decorative
        spinners on the page.
        """
        self.set_step("searching")
        button = self.wait().until(EC.element_to_be_clickable(SEL["search_button"]))
        self.scroll_into_view(button)
        baseline = self._spinner_count()
        button.click()
        try:
            # A new spinner over the baseline means the search fired.
            self.wait(8).until(lambda _d: self._spinner_count() > baseline)
            spinner_seen = True
        except TimeoutException:
            spinner_seen = False

        if spinner_seen:
            # Wait for it to settle back to baseline — the search has finished.
            try:
                self.wait(30).until(lambda _d: self._spinner_count() <= baseline)
            except TimeoutException:
                pass
        else:
            # Instant/cached result, or a click that never fired. Give the grid a
            # short beat to populate before we decide it's empty.
            try:
                self.wait(8).until(EC.presence_of_element_located(SEL["results_rows"]))
            except TimeoutException:
                pass
        time.sleep(1)

        if self.driver.find_elements(*SEL["results_rows"]):
            return self.RESULTS
        if spinner_seen:
            return self.EMPTY
        # Never saw the search run and no rows appeared — surface it as empty for
        # the operator, but screenshot it so a silently-broken search is auditable.
        self.screenshot("empty_unconfirmed")
        return self.EMPTY_UNCONFIRMED

    def _spinner_count(self) -> int:
        """Number of currently-*displayed* progress spinners. Hidden spinners the
        portal keeps in the DOM at rest don't count; a search adds a visible one."""
        count = 0
        for element in self.driver.find_elements(*SEL["progress_spinner"]):
            try:
                if element.is_displayed():
                    count += 1
            except WebDriverException:
                pass
        return count

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

    def _robust_click(self, target, attempts: int = 3) -> None:
        """Click something an overlay or a still-rendering card may be sitting on top of.

        `target` is a locator tuple or an already-resolved element. Each attempt
        re-resolves a locator (the element may have been re-rendered underneath us),
        scrolls it into view and waits out any backdrop before clicking. The last
        attempt falls back to the JS click used elsewhere in this file, which fires
        the handler regardless of what is painted over the element.
        """
        for attempt in range(1, attempts + 1):
            try:
                element = (
                    self.wait().until(EC.element_to_be_clickable(target))
                    if isinstance(target, tuple)
                    else target
                )
                self.scroll_into_view(element)
                self._wait_no_backdrop()
                if attempt == attempts:
                    self.driver.execute_script("arguments[0].click();", element)
                else:
                    element.click()
                return
            except (TimeoutException, WebDriverException):
                if attempt == attempts:
                    raise
                time.sleep(1)

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

        link = self.wait(BID_PAGE_TIMEOUT).until(EC.element_to_be_clickable((By.LINK_TEXT, number)))
        self.scroll_into_view(link)
        link.click()
        self.wait(BID_PAGE_TIMEOUT).until(lambda d: "/detail/" in d.current_url)
        time.sleep(DETAIL_RENDER_SECONDS)  # the route resolving is not the render

        bid_dir = storage.bid_folder(self.run_dir, number, title)

        doc_links = self.driver.find_elements(*SEL["document_links"])
        for index, doc_link in enumerate(doc_links, start=1):
            try:
                self.scroll_into_view(doc_link)
                # JS click: a stray CDK backdrop can intercept a native click, and
                # the anchor's download is driven by its click handler regardless.
                self.driver.execute_script("arguments[0].click();", doc_link)
                downloaded = self.wait_for_download()
                # Keep the portal's real filename; only prefix an index if two
                # attachments on this bid happen to share a name.
                target = bid_dir / downloaded.name
                if target.exists():
                    target = bid_dir / f"{index}_{downloaded.name}"
                shutil.move(str(downloaded), str(target))
                result["documents"].append(target.name)
            except (TimeoutException, WebDriverException, OSError) as exc:
                result.setdefault("document_errors", []).append(f"doc {index}: {exc.__class__.__name__}")

        self.driver.back()
        self.wait(BID_PAGE_TIMEOUT).until(EC.presence_of_element_located(SEL["results_rows"]))
        time.sleep(1)
        return result

    def _recover_to_results(self) -> None:
        """After a bid errors on its detail page, step back so the next bid's
        Number link is reachable again. No-op if already on the results list."""
        try:
            if "/detail/" in self.driver.current_url:
                self.driver.back()
                self.wait(BID_PAGE_TIMEOUT).until(
                    EC.presence_of_element_located(SEL["results_rows"])
                )
                time.sleep(1)
        except (TimeoutException, WebDriverException):
            pass

    def _bid_folder_name(self, bid: dict) -> str:
        """Where this bid's documents live *inside the archive* — the value the
        summary sheet's Folder column carries, so a row points at a real place in
        the unpacked ZIP. See `myflorida/storage.py` for the layout."""
        return storage.folder_reference(bid.get("number") or "", bid.get("title") or "")

    def _niche_label(self) -> str:
        """Human label for the run's niche, used for the merged workbook name."""
        run = run_manager.get_run(self.run_id) or {}
        return run.get("category_label") or run.get("category") or "MyFlorida"

    def export_excel(self, suffix: str = "") -> Path:
        """Export the current result set into the run's `_exports/` staging folder.

        `suffix` keeps one keyword's export from overwriting the next one's, since a
        keyword run exports once per pass. The raw per-keyword exports are stashed
        here and stitched into one `<Niche>_bids.xlsx` at the end (see _finalize)."""
        self.set_step("exporting_excel")
        button = self.wait().until(EC.element_to_be_clickable(SEL["export_excel"]))
        button.click()
        downloaded = self.wait_for_download()
        exports_dir = self.run_dir / "_exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        name = f"bids_export_{sanitize_filename(suffix)}" if suffix else "bids_export"
        target = exports_dir / f"{name}{downloaded.suffix or '.xlsx'}"
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

    def _search_pass(self, keyword: str | None = None) -> tuple[str, list[dict]]:
        """Fill in one Advanced Search; return (outcome, rows).

        Assumes the ads page is open. A keyword pass searches on the keyword; a
        code pass selects the run's commodity codes. Ad Status/Type apply to both.
        `outcome` is one of RESULTS/EMPTY/EMPTY_UNCONFIRMED; rows are only read
        when the search returned some.
        """
        self.open_advanced_search()
        if keyword is None:
            self.enter_commodity_codes()
        else:
            self.enter_keyword(keyword)
        self.select_ad_status()
        self.select_ad_type()
        # Last, so nothing that expands a panel or closes an overlay can land on
        # top of the value between typing it and pressing Search.
        self.apply_date_range()
        outcome = self.submit_search()
        bids = self.collect_bids() if outcome == self.RESULTS else []
        return outcome, bids

    def _export(self, suffix: str = "") -> Path | None:
        """Capture the current result set to the exports staging folder before
        crawling documents, so a failure in the per-bid crawl still leaves the
        run's data on disk. Returns the export path, or None if the export failed."""
        try:
            return self.export_excel(suffix)
        except (TimeoutException, WebDriverException) as exc:
            run_manager.add_error(self.run_id, f"excel export failed: {exc.__class__.__name__}")
            self.screenshot("export_excel")
            return None

    def _finalize(self, exports: list[Path], found: dict[str, dict]) -> None:
        """Merge the per-pass exports into one workbook, then ingest it once.

        `found` maps ad number -> the accumulated bid dict (carrying matched
        keywords), used to fill the merged workbook's Matched Keyword / Folder
        columns. Both steps are best-effort: the files on disk are the source of
        truth, so neither a merge nor a DB failure fails the run."""
        if not exports:
            return
        self.set_step("merging_workbook")
        keyword_by_ad = {num: ", ".join(entry.get("matched_keywords", [])) for num, entry in found.items()}
        folder_by_ad = {num: self._bid_folder_name(entry) for num, entry in found.items()}
        try:
            self.excel_path = merge_exports(
                exports, self.run_dir, self._niche_label(), keyword_by_ad, folder_by_ad
            )
        except Exception as exc:  # noqa: BLE001 — a merge failure shouldn't fail the run
            logger.exception("[run %s] workbook merge failed", self.run_id)
            run_manager.add_error(self.run_id, f"workbook merge failed: {exc.__class__.__name__}")
            return

        # No close-date pruning, and nothing else that drops rows: every
        # advertisement the portal returned reaches the summary sheet and the DB.
        # The workbook used to be filtered to ads at least MIN_DAYS_UNTIL_CLOSE
        # days from closing, which silently removed bids a reviewer might still
        # have wanted to see — judging what is worth pursuing is the reviewer's,
        # not the scraper's. `app.core.closing_filter` still exists for the
        # portals that use it; this flow no longer calls it.
        try:
            self.ingest_to_db()
        except Exception as exc:  # noqa: BLE001 — DB issues shouldn't fail the run
            logger.exception("[run %s] DB ingestion failed", self.run_id)
            run_manager.add_error(self.run_id, f"db ingestion failed: {exc.__class__.__name__}")

    def _process_bids(self, bids: list[dict], processed: set[str]) -> None:
        """Download each bid's documents, skipping ones already done in this run.

        `processed` carries across keyword passes: an ad that several keywords
        match is downloaded once, and the pass that first found it wins.

        A bid that times out is attempted again rather than written off on the
        first try. The usual cause is the results grid or the detail route being
        slow for a moment — a longer wait does not help with that, because by
        then the browser is on the wrong page and waiting on a link that is not
        coming. Stepping back to the results list and asking again is what
        actually recovers it, and a bid lost here is a bid whose attachments are
        missing from the export with only a line in the error list to say so.
        """
        for bid in bids:
            if bid["number"] in processed:
                continue
            processed.add(bid["number"])
            run_manager.add_bid_result(self.run_id, self._process_one_bid(bid))

    def _process_one_bid(self, bid: dict) -> dict:
        """One bid, retried up to BID_ATTEMPTS times; never raises."""
        number = bid["number"]
        for attempt in range(1, BID_ATTEMPTS + 1):
            try:
                return self.process_bid(bid)
            except (TimeoutException, WebDriverException) as exc:
                last = exc
                # Only the attempt that gives up is worth a picture; one per try
                # would fill the run folder with the same screen twice.
                if attempt == BID_ATTEMPTS:
                    self.screenshot(f"bid_{number}")
                else:
                    logger.warning(
                        "[run %s] bid %s: %s on attempt %d — retrying",
                        self.run_id, number, exc.__class__.__name__, attempt,
                    )
                # Back to the results list either way: the next attempt and the
                # next bid both need the Number links reachable again.
                self._recover_to_results()
        run_manager.add_error(
            self.run_id,
            f"bid {number}: {last.__class__.__name__} after {BID_ATTEMPTS} attempts",
        )
        return {**bid, "documents": [], "error": str(last)[:300]}

    def _run_codes(self) -> None:
        """One search across every selected commodity code."""
        self.open_advertisements()
        _outcome, bids = self._search_pass()
        if not bids:
            run_manager.add_warning(self.run_id, "no bids found for the selected commodity codes")
            run_manager.update_run(self.run_id, no_results=True)
            return
        run_manager.update_run(self.run_id, bids_found=len(bids))
        export = self._export()
        self._process_bids(bids, set())
        # No keyword in code mode — folder column still wants the ad->bid mapping.
        found = {b["number"]: {**b, "matched_keywords": []} for b in bids}
        self._finalize([export] if export else [], found)

    def _run_keywords(self) -> None:
        """One search per keyword, exports merged into one workbook by ad number.

        Each pass restarts from the ads page so the form is clean rather than
        carrying the previous keyword's criteria. A keyword that matches nothing
        records a warning and exports nothing (no header-only workbook); if every
        keyword comes back empty the run is flagged no_results.
        """
        processed: set[str] = set()
        found: dict[str, dict] = {}
        exports: list[Path] = []
        any_results = False
        for index, keyword in enumerate(self.keywords, start=1):
            run_manager.update_run(self.run_id, keyword=keyword, keyword_progress=f"{index}/{len(self.keywords)}")
            if index > 1:
                time.sleep(KEYWORD_PAUSE_SECONDS)
            try:
                self.open_advertisements()
                _outcome, bids = self._search_pass(keyword)
            except (TimeoutException, WebDriverException) as exc:
                run_manager.add_error(self.run_id, f"keyword {keyword!r}: search failed ({exc.__class__.__name__})")
                self.screenshot(f"keyword_{sanitize_filename(keyword)}")
                continue
            if not bids:
                run_manager.add_warning(self.run_id, f"keyword '{keyword}' — no bids found")
                continue
            any_results = True
            for bid in bids:
                # Accumulate every keyword that surfaced the ad (comma-joined later).
                entry = found.get(bid["number"])
                if entry is None:
                    found[bid["number"]] = {**bid, "matched_keywords": [keyword]}
                elif keyword not in entry["matched_keywords"]:
                    entry["matched_keywords"].append(keyword)
            run_manager.update_run(self.run_id, bids_found=len(found))
            export = self._export(keyword)
            if export:
                exports.append(export)
            self._process_bids([found[b["number"]] for b in bids], processed)
        if not any_results:
            run_manager.update_run(self.run_id, no_results=True)
        self._finalize(exports, found)

    def _select_account(self) -> None:
        """Resolve this run's login and confirm it can actually be used.

        The endpoint already checked this before creating the run; doing it
        again here covers a run started any other way, and means the browser is
        never launched for a login that cannot succeed. On MFMP that is worth
        more than elsewhere: the browser opens *visible* and a person waits at
        it to type a one-time password, so a run that was never going to sign in
        wastes someone's attention rather than just a process. `require` raises
        with the `.env` keys to fix, which becomes the run's error.
        """
        self.set_step("selecting_account")
        requested = (run_manager.get_run(self.run_id) or {}).get("account")
        self.account = accounts.require(requested)
        # Key and label only: the run state goes to the console. The address is
        # logged instead, masked, where it helps and is not on anyone's screen.
        run_manager.update_run(
            self.run_id,
            account=self.account.key,
            account_label=self.account.label,
        )
        logger.info("[JOB INITIALIZED]: Portal: MyFloridaMarketPlace (MFMP)")
        logger.info(" ├── [ACCOUNT SELECTED]: %s (%s)",
                    self.account.label, accounts.mask(self.account.username))

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        try:
            # Before the browser, so the console shows what the window is doing
            # from the first poll rather than after a login that waits on a
            # person to type a one-time password.
            self.report_date_window()
            self._select_account()
            # Visible, always, while manual OTP is on: the run stops at the
            # one-time password and waits for a person to type it in, and there
            # is nothing to type into in a headless window. Not left to the
            # per-run "Live preview" flag — a run started without it would hang
            # at the challenge until the wait expired, with no way to answer.
            logger.info(" ├── [LAUNCHING BROWSER]: %s",
                        "Headed mode for manual OTP verification..."
                        if settings.mfmp_manual_otp else "Manual OTP is off for this deployment")
            self.start_driver(headless=False if settings.mfmp_manual_otp else None)
            self.login()
            if self.keyword_mode:
                self._run_keywords()
            else:
                self._run_codes()
            # Package the run into one archive ZIP — the merged workbook plus
            # every bid's document folder — then delete the workspace.
            self.set_step("packaging_results")
            archive_run(self.run_id)

            run_manager.update_run(self.run_id, status="completed", step="done")
            # Email/S3 notification on successful completion (attaches the run
            # ZIP, or the merged workbook if the ZIP is too big to email).
            final = run_manager.get_run(self.run_id) or {}
            notify_scrape_completion(self.run_id, "myflorida", final.get("bids_found", 0))
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
            run_manager.add_error(self.run_id, describe_error(exc, self.current_step))
            run_manager.update_run(self.run_id, status="failed", step="failed")
        finally:
            self.cleanup()
            run_manager.update_run(self.run_id, finished_at=datetime.now().isoformat())
            run_manager.remove_empty_folder(self.run_id)


def execute_run(
    run_id: str,
    codes: list[str],
    ad_statuses: list[str] | None = None,
    ad_types: list[str] | None = None,
    keywords: list[str] | None = None,
    date_range: dates.PostingDateRange | None = None,
) -> None:
    MFMPScraper(run_id, codes, ad_statuses, ad_types, keywords, date_range).run()
