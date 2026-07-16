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

    def open_advertisements(self) -> None:
        self.set_step("opening_advertisements")
        self.driver.get(ADS_URL)
        self.wait().until(EC.element_to_be_clickable(SEL["advanced_search_button"]))

    def open_advanced_search(self) -> None:
        self.set_step("opening_advanced_search")
        self.wait().until(EC.element_to_be_clickable(SEL["advanced_search_button"])).click()
        self._set_max_results(MAX_RESULTS)
        if not self.keyword_mode:
            # Expand the Commodity Codes accordion so its multi-select renders.
            header = self.wait().until(EC.element_to_be_clickable(SEL["commodity_panel_header"]))
            self.scroll_into_view(header)
            if header.get_attribute("aria-expanded") != "true":
                header.click()
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
            header = self.wait().until(EC.element_to_be_clickable(SEL[header_key]))
            self.scroll_into_view(header)
            if header.get_attribute("aria-expanded") != "true":
                header.click()
            options = self.wait().until(EC.presence_of_all_elements_located(SEL[options_key]))
            matched = set()
            for option in options:
                # Read textContent rather than .text: the label lives in a nested
                # .mat-list-text div and Selenium's .text returns "" for options that
                # are below the fold or mid-animation, which would spuriously report
                # every option as "option not found".
                text = (option.get_attribute("textContent") or "").strip().lower()
                if text in wanted:
                    self.scroll_into_view(option)
                    option.click()
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

    def submit_search(self) -> None:
        self.set_step("searching")
        button = self.wait().until(EC.element_to_be_clickable(SEL["search_button"]))
        self.scroll_into_view(button)
        button.click()
        # Results render asynchronously; tolerate an empty result set.
        try:
            self.wait(20).until(EC.presence_of_element_located(SEL["results_rows"]))
        except TimeoutException:
            pass
        time.sleep(2)

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

        bid_dir = self.run_dir / sanitize_filename(title or number)
        bid_dir.mkdir(parents=True, exist_ok=True)

        doc_links = self.driver.find_elements(*SEL["document_links"])
        for index, doc_link in enumerate(doc_links, start=1):
            try:
                self.scroll_into_view(doc_link)
                # JS click: a stray CDK backdrop can intercept a native click, and
                # the anchor's download is driven by its click handler regardless.
                self.driver.execute_script("arguments[0].click();", doc_link)
                downloaded = self.wait_for_download()
                new_name = f"{sanitize_filename(title or number)}_{index}{downloaded.suffix}"
                target = bid_dir / new_name
                shutil.move(str(downloaded), str(target))
                result["documents"].append(new_name)
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

    def export_excel(self, suffix: str = "") -> Path:
        """Export the current result set. `suffix` keeps one keyword's export from
        overwriting the next one's, since a keyword run exports once per pass."""
        self.set_step("exporting_excel")
        button = self.wait().until(EC.element_to_be_clickable(SEL["export_excel"]))
        button.click()
        downloaded = self.wait_for_download()
        name = f"bids_export_{sanitize_filename(suffix)}" if suffix else "bids_export"
        target = self.run_dir / f"{name}{downloaded.suffix or '.xlsx'}"
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

    def _search_pass(self, keyword: str | None = None) -> list[dict]:
        """Fill in one Advanced Search and return its result rows.

        Assumes the ads page is open. A keyword pass searches on the keyword; a
        code pass selects the run's commodity codes. Ad Status/Type apply to both.
        """
        self.open_advanced_search()
        if keyword is None:
            self.enter_commodity_codes()
        else:
            self.enter_keyword(keyword)
        self.select_ad_status()
        self.select_ad_type()
        self.submit_search()
        return self.collect_bids()

    def _export_and_ingest(self, suffix: str = "") -> None:
        """Capture the current result set before crawling documents, so a failure
        in the per-bid crawl still leaves the run's data on disk and in the DB."""
        try:
            self.export_excel(suffix)
        except (TimeoutException, WebDriverException) as exc:
            run_manager.add_error(self.run_id, f"excel export failed: {exc.__class__.__name__}")
            self.screenshot("export_excel")
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
        bids = self._search_pass()
        run_manager.update_run(self.run_id, bids_found=len(bids))
        self._export_and_ingest()
        self._process_bids(bids, set())

    def _run_keywords(self) -> None:
        """One search per keyword, each with its own export, merged by ad number.

        Each pass restarts from the ads page so the form is clean rather than
        carrying the previous keyword's criteria.
        """
        processed: set[str] = set()
        found: dict[str, dict] = {}
        for index, keyword in enumerate(self.keywords, start=1):
            run_manager.update_run(self.run_id, keyword=keyword, keyword_progress=f"{index}/{len(self.keywords)}")
            try:
                self.open_advertisements()
                bids = self._search_pass(keyword)
            except (TimeoutException, WebDriverException) as exc:
                run_manager.add_error(self.run_id, f"keyword {keyword!r}: search failed ({exc.__class__.__name__})")
                self.screenshot(f"keyword_{sanitize_filename(keyword)}")
                continue
            for bid in bids:
                # Record which keyword surfaced the ad; first match wins.
                found.setdefault(bid["number"], {**bid, "matched_keyword": keyword})
            run_manager.update_run(self.run_id, bids_found=len(found))
            self._export_and_ingest(keyword)
            self._process_bids([found[b["number"]] for b in bids], processed)

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        try:
            self.start_driver()
            self.login()
            if self.keyword_mode:
                self._run_keywords()
            else:
                self._run_codes()
            run_manager.update_run(self.run_id, status="completed", step="done")
        except Exception as exc:  # noqa: BLE001 — a failed run must be reported, not crash the worker
            logger.exception("[run %s] failed", self.run_id)
            self.screenshot("fatal")
            run_manager.add_error(self.run_id, describe_error(exc, self.current_step))
            run_manager.update_run(self.run_id, status="failed", step="failed")
        finally:
            self.cleanup()
            run_manager.update_run(self.run_id, finished_at=datetime.now().isoformat())


def execute_run(
    run_id: str,
    codes: list[str],
    ad_statuses: list[str] | None = None,
    ad_types: list[str] | None = None,
    keywords: list[str] | None = None,
) -> None:
    MFMPScraper(run_id, codes, ad_statuses, ad_types, keywords).run()
