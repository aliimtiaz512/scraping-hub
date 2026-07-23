"""Selenium automation for the MFMP vendor portal.

Flow: login -> Advertisements -> Advanced Search -> search criteria -> Search ->
Export Excel -> store in DB -> open each bid -> download documents.

A run searches one of two ways, decided by execute_run's arguments:
  commodity codes — a single search with every code selected.
  keywords        — one search per keyword, results merged and de-duplicated by
                    ad number so an ad several keywords match downloads once.

The portal is an Angular Material single-page app. Selectors below were verified
against the live site; the fiddly parts are the commodity-code control (a
ngx-mat-select-search multi-select whose options load asynchronously and which
stays disabled until they do) and the CDK overlay backdrops that intercept
clicks until they finish animating out.
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
from app.core.base_scraper import BaseScraper
from app.core.filenames import sanitize_filename
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
    "max_results_select": (By.XPATH, "//mat-form-field[.//mat-label[contains(.,'Maximum')]]//mat-select"),
    "ad_status_panel_header": (By.XPATH, "//mat-expansion-panel-header[.//mat-panel-title[contains(normalize-space(.),'Ad Status')]]"),
    "ad_status_options": (By.XPATH, "//mat-selection-list[@aria-label='Ad Status']//mat-list-option"),
    "ad_type_panel_header": (By.XPATH, "//mat-expansion-panel-header[.//mat-panel-title[contains(normalize-space(.),'Ad Type')]]"),
    "ad_type_options": (By.XPATH, "//mat-selection-list[@aria-label='Ad Type']//mat-list-option"),
    # The only free-text field on Advanced Search; capped at 100 chars by the portal.
    "title_input": (By.CSS_SELECTOR, "input[formcontrolname='title']"),
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
    ):
        super().__init__(run_id)
        self.codes = codes
        self.keywords = [k.strip() for k in (keywords or []) if k.strip()]
        # An empty list means: leave the portal's Ad Status list untouched (every status).
        self.ad_statuses = [s for s in (ad_statuses or []) if s in AD_STATUS_LABELS]
        # Likewise for Ad Type — empty means every type.
        self.ad_types = [t for t in (ad_types or []) if t in AD_TYPE_LABELS]
        self.excel_path: Path | None = None

    @property
    def keyword_mode(self) -> bool:
        return bool(self.keywords)

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
        email.clear()
        email.send_keys(settings.mfmp_email)
        password = self.driver.find_element(*SEL["login_password"])
        password.clear()
        password.send_keys(settings.mfmp_password)
        self.driver.find_element(*SEL["login_submit"]).click()
        # Successful login leaves the /login page.
        self.wait().until(lambda d: "login" not in d.current_url.lower())

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

        link = self.wait().until(EC.element_to_be_clickable((By.LINK_TEXT, number)))
        self.scroll_into_view(link)
        link.click()
        self.wait().until(lambda d: "/detail/" in d.current_url)
        time.sleep(2)  # let the detail page (incl. document list) render

        bid_dir = self.run_dir / self._bid_folder_name(bid)
        bid_dir.mkdir(parents=True, exist_ok=True)

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
        self.wait().until(EC.presence_of_element_located(SEL["results_rows"]))
        time.sleep(1)
        return result

    def _recover_to_results(self) -> None:
        """After a bid errors on its detail page, step back so the next bid's
        Number link is reachable again. No-op if already on the results list."""
        try:
            if "/detail/" in self.driver.current_url:
                self.driver.back()
                self.wait().until(EC.presence_of_element_located(SEL["results_rows"]))
                time.sleep(1)
        except (TimeoutException, WebDriverException):
            pass

    def _bid_folder_name(self, bid: dict) -> str:
        """`<ad number>_<title>` — the ad number leads because it's the stable ID
        you'd search by; the title (truncated) follows for readability."""
        number = sanitize_filename((bid.get("number") or "").strip(), max_length=64)
        # sanitize_filename truncates after its own strip, so a title cut mid-word
        # can still end in a space or dot — strip again on this side of the cut.
        title = sanitize_filename((bid.get("title") or "").strip(), max_length=60).strip(" ._")
        if number and title:
            return f"{number}_{title}"
        return number or title or "untitled"

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
        try:
            self.ingest_to_db()
        except Exception as exc:  # noqa: BLE001 — DB issues shouldn't fail the run
            logger.exception("[run %s] DB ingestion failed", self.run_id)
            run_manager.add_error(self.run_id, f"db ingestion failed: {exc.__class__.__name__}")

    def _process_bids(self, bids: list[dict], processed: set[str]) -> None:
        """Download each bid's documents, skipping ones already done in this run.

        `processed` carries across keyword passes: an ad that several keywords
        match is downloaded once, and the pass that first found it wins.
        """
        for bid in bids:
            if bid["number"] in processed:
                continue
            processed.add(bid["number"])
            try:
                result = self.process_bid(bid)
            except (TimeoutException, WebDriverException) as exc:
                result = {**bid, "documents": [], "error": str(exc)[:300]}
                run_manager.add_error(self.run_id, f"bid {bid['number']}: {exc.__class__.__name__}")
                self.screenshot(f"bid_{bid['number']}")
                self._recover_to_results()
            run_manager.add_bid_result(self.run_id, result)

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

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        try:
            self.start_driver()
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
) -> None:
    MFMPScraper(run_id, codes, ad_statuses, ad_types, keywords).run()
