"""Selenium automation for the BidNet Direct vendor portal.

A run searches **one niche**, and every keyword that niche owns is searched
**separately, one at a time, in a single browser session** — never combined into
a boolean query. A combined `A AND B` search only returns solicitations matching
both terms, a small fraction of what the terms find individually; searching them
in sequence is what makes the bid count whole. Keywords come from the database
(see `niches.py`); the frontend only ever sends a niche key.

Flow, per run:

    login
    for each keyword of the niche:          <- sequential, same session
        ensure_logged_in                    <- re-login if the session expired
        search(keyword)
        result_count() == 0 ?  -> skip      <- fast-fail, no waiting on empty results
        filter to "Member Agency Bids"
        apply the frontend's sidebar filters
        paginate, collecting solicitation links
    for each distinct solicitation link:    <- deduplicated across all keywords
        open it, scrape its fields, download every document
    write one master Excel, persist to the DB, package the run

Everything lands in **one project folder** — per-bid document subfolders and the
master spreadsheet at its root — because a run is a single niche and there is
nothing to split apart.
"""

import logging
import shutil
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.config import settings
from app.core import run_manager
from app.core.base_scraper import BaseScraper, StopRequested
from app.core.closing_filter import MIN_DAYS_UNTIL_CLOSE, days_until_close
from app.db import SessionLocal
from app.scrapers.bidnet import export, niches
from app.scrapers.bidnet.filters import SidebarFilterRequest
from app.scrapers.bidnet.sidebar import SidebarDriver
from app.core.exports import archive_run, excel_name
from app.services.notifier import notify_scrape_completion

logger = logging.getLogger(__name__)

BASE_URL = "https://www.bidnetdirect.com"

# Timeouts are deliberately generous: a niche is searched one keyword at a time
# in a single session, so a run is a long sequence of page loads rather than a
# quick burst, and BidNet slows down noticeably the longer a session lives.
# Failing a whole keyword's search on a page that was merely slow costs far more
# than waiting a little longer for it.
ELEMENT_TIMEOUT = 60       # search box, results table, detail-page fields
PAGINATION_TIMEOUT = 30    # next page of results — absent rows can be a real end-of-list
DETAIL_TIMEOUT = 45        # solicitation detail page and its documents tab
DOC_DOWNLOAD_TIMEOUT = 90  # per-document; a missing download falls back to a direct fetch
SEARCH_SETTLE_SECONDS = 5  # after switching result group / opening the documents tab
MAX_PAGES = 100  # pagination safety guard, same as the original

# The result-group tab the scraper scrapes ("Member Agency Bids"). Its header
# carries the authoritative hit count for the current search:
#
#   <div class="searchContentGroupContainer" search-content-group-id="2085061601">
#     <span class="solicitationCount">1,848</span> … Member Agency Bids
#
# which is what lets a zero-result keyword be skipped without waiting on rows
# that are never coming. See `result_count`.
MEMBER_AGENCY_GROUP_ID = "2085061601"
RESULTS_ROW = "table tbody tr.mets-table-row"

# How long to let the search's own AJAX finish before reading that count. Until
# it does, the tab still shows the *previous* keyword's number, so reading early
# gives a confidently wrong answer rather than a slow one.
AJAX_IDLE_TIMEOUT = 30

# Fields scraped from each solicitation detail page: db column -> visible label.
DETAIL_FIELDS: dict[str, str] = {
    "reference_number": "Reference Number",
    "solicitation_number": "Solicitation Number",
    "solicitation_type": "Solicitation Type",
    "title": "Title",
    "publication_date": "Publication",
    "question_acceptance_deadline": "Question Acceptance Deadline",
    "closing_date": "Closing Date",
}


def _safe_title(title: str) -> str:
    cleaned = "".join(c for c in title if c.isalpha() or c.isdigit() or c == " ").rstrip()
    return cleaned or "Bid"


class BidnetScraper(BaseScraper):
    def __init__(
        self,
        run_id: str,
        keywords: list[str],
        filters: SidebarFilterRequest | None = None,
        niche_label: str | None = None,
    ):
        super().__init__(run_id)
        # Every keyword of the run's single niche, resolved from the database by
        # the router. They are searched one at a time, never combined.
        self.keywords = keywords
        self.niche_label = niche_label or "BidNet"
        # The sidebar filter state chosen in the frontend. Defaults to the
        # portal's own defaults (Open Solicitations, every purchasing group, no
        # other constraint), so an omitted request scrapes exactly what the
        # pre-filter scraper did.
        self.filters = filters or SidebarFilterRequest()
        self._sidebar_report: dict | None = None
        # Sidebar problems already reported. The filters are re-applied for every
        # keyword, so a persistent one (an option the portal stopped offering)
        # would otherwise be logged once per search.
        self._sidebar_notes: set[str] = set()
        # The single project folder for this whole run: every keyword's bids and
        # documents land here in per-bid subfolders, with the master Excel at the
        # root. A run is one niche, so there is nothing to split apart.
        run = run_manager.get_run(run_id) or {}
        folder = run.get("folder") or str(settings.work_root / "Bidnetdirect")
        self.document_folder = Path(folder)
        self.document_folder.mkdir(parents=True, exist_ok=True)
        # Keywords the portal reported zero bids for — skipped without waiting
        # on rows that were never coming, and surfaced on the run so a niche
        # whose terms all miss is obvious rather than silent.
        self._empty_keywords: list[str] = []
        # Close-date filter tallies (see app/core/closing_filter): solicitations
        # dropped for closing too soon (before their documents are downloaded),
        # and those kept despite an unreadable Closing Date.
        self._skipped_closing_soon = 0
        self._kept_unreadable_close = 0

    # -- helpers ------------------------------------------------------------

    def _extract_field(self, field_name: str) -> str:
        """Read a .mets-field body paragraph whose field contains the label."""
        xpath = (
            f"//div[contains(@class,'mets-field')][contains(., \"{field_name}\")]"
            f"//div[contains(@class,'mets-field-body')]//p"
        )
        try:
            return self.driver.find_element(By.XPATH, xpath).text
        except WebDriverException as exc:
            logger.info("[run %s] failed to extract %s: %s", self.run_id, field_name, exc.__class__.__name__)
            return ""

    def _guard_not_blocked(self) -> None:
        """Fail fast with a clear message if the portal served a bot-block page.

        Without this, a 403/"Access Denied" landing page has none of the expected
        elements, so the next wait dies with an empty-message TimeoutException that
        gives no clue why the run failed.
        """
        title = (self.driver.title or "").lower()
        try:
            heading = self.driver.find_element(By.TAG_NAME, "body").text[:200].lower()
        except WebDriverException:
            heading = ""
        markers = ("403 forbidden", "access denied", "request unsuccessful", "pardon our interruption")
        if any(m in title or m in heading for m in markers):
            self.screenshot("blocked")
            raise WebDriverException(
                "BidNet Direct returned a bot-block page (e.g. 403 Forbidden). "
                "The portal is refusing the automated browser."
            )

    def _abs_url(self, href: str) -> str:
        if href.startswith("/"):
            return BASE_URL + href
        return href

    # -- flow steps ---------------------------------------------------------

    def login(self) -> None:
        self.set_step("logging_in")
        self.driver.get(settings.bidnet_direct_link or BASE_URL)
        self._guard_not_blocked()

        # Each wait re-checks for a bot-block first, then screenshots and raises a
        # clear message on timeout — otherwise BidNet's interstitial (which appears
        # a beat after load) just makes the element wait die with an empty-message
        # Selenium stacktrace that says nothing about why.
        self._await_login_element((By.ID, "header_btnLogin"), "the Login button", clickable=True).click()

        self._await_login_element((By.ID, "j_username"), "the username field")
        self.driver.find_element(By.ID, "j_username").send_keys(settings.bidnet_username)
        self.driver.find_element(By.ID, "j_password").send_keys(settings.bidnet_password)
        self.driver.find_element(By.ID, "loginButton").click()

        self._await_login_element(
            (By.ID, "btnSolicitations"),
            "the post-login dashboard (Solicitations menu)",
        )

    def _await_login_element(self, locator: tuple, what: str, clickable: bool = False):
        """Wait for a login-flow element, turning a timeout into a clear message.

        On timeout we re-run the bot-block check (the interstitial often appears
        just after the initial load) and always screenshot the login page, so a
        failure says whether BidNet blocked us or the page was simply slow — never
        the bare empty-message Selenium stacktrace.
        """
        condition = EC.element_to_be_clickable(locator) if clickable else EC.presence_of_element_located(locator)
        try:
            return self.wait().until(condition)
        except TimeoutException as exc:
            self.screenshot("login_page")
            # Raises a clear "bot-block" message if the block markers are present.
            self._guard_not_blocked()
            raise WebDriverException(
                f"BidNet Direct login timed out waiting for {what}. The login page "
                "did not present the expected element within the wait — most often "
                "this is BidNet's anti-bot protection throttling repeated automated "
                "logins (try again later / from a different network, and confirm the "
                "account still signs in through a normal browser)."
            ) from exc

    def ensure_logged_in(self) -> None:
        """Re-login if BidNet has dropped the session, before the next search.

        A niche of twenty-odd keywords runs for hours, and the portal will expire
        the session partway through. That does not surface as a timeout — the
        page simply becomes the login screen, and every subsequent search fails
        for a reason no timeout increase fixes. Cheap to check (one DOM lookup
        for the post-login menu), so it runs before every keyword.
        """
        try:
            if self.driver.find_elements(By.ID, "btnSolicitations"):
                return  # still signed in
        except WebDriverException:
            pass  # fall through and try to recover

        logger.info("[run %s] session lost — signing in again", self.run_id)
        run_manager.add_error(self.run_id, "BidNet session expired mid-run; signed in again")
        self.login()

    def search(self, keyword: str) -> None:
        self.set_step(f"searching: {keyword}")
        box = self.wait(ELEMENT_TIMEOUT).until(
            EC.presence_of_element_located((By.ID, "solicitationSingleBoxSearch"))
        )
        box.clear()
        box.send_keys(keyword)
        self.driver.find_element(By.ID, "topSearchButton").click()
        self.wait(ELEMENT_TIMEOUT).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, ".searchContentGroupContainer"))
        )
        # Wait for the search's own AJAX rather than a fixed sleep: the group
        # tabs are re-rendered by it, and until it lands they still show the
        # previous keyword's counts.
        self._await_ajax_idle()

    def _await_ajax_idle(self, timeout: int = AJAX_IDLE_TIMEOUT) -> bool:
        """Block until the page has no jQuery request in flight.

        The portal is jQuery-driven, so `jQuery.active == 0` is its own signal
        that a search has finished rendering — far more reliable than sleeping a
        guessed number of seconds. Returns False if jQuery is unavailable or the
        wait ran out, so callers can fall back rather than assume.
        """
        script = "return (typeof jQuery === 'undefined') ? null : jQuery.active;"
        try:
            return bool(
                WebDriverWait(self.driver, timeout).until(
                    lambda d: d.execute_script(script) == 0
                )
            )
        except (TimeoutException, WebDriverException):
            logger.info("[run %s] AJAX did not go idle within %ss", self.run_id, timeout)
            return False

    def result_count(self) -> int | None:
        """How many bids the Member Agency group holds for the current search.

        Read from that tab's own `.solicitationCount`, which the portal fills in
        for every search — 0 when nothing matched. Returns None when the element
        is missing or unparseable, which callers must treat as "unknown" and
        carry on: skipping a keyword we could not read would silently lose bids.
        """
        script = """
        const g = document.querySelector("div[search-content-group-id='" + arguments[0] + "']");
        if (!g) return null;
        const n = g.querySelector('.solicitationCount');
        if (!n) return null;
        const digits = (n.textContent || '').replace(/[^0-9]/g, '');   // "1,848" -> "1848"
        return digits === '' ? null : parseInt(digits, 10);
        """
        try:
            value = self.driver.execute_script(script, MEMBER_AGENCY_GROUP_ID)
        except WebDriverException as exc:
            logger.info("[run %s] could not read result count: %s", self.run_id, exc.__class__.__name__)
            return None
        return int(value) if isinstance(value, (int, float)) else None

    def filter_member_agency(self) -> None:
        self.set_step("filtering_member_agency")
        self.driver.find_element(
            By.CSS_SELECTOR, f"div[search-content-group-id='{MEMBER_AGENCY_GROUP_ID}']"
        ).click()
        self._await_ajax_idle()
        # Conditional, not a blind wait: the caller only gets here when the group
        # reported a non-zero count, so rows are expected — but if they somehow
        # do not arrive, `collect_links` simply finds none. Waiting the full
        # element timeout for rows that are not coming is what used to turn an
        # empty search into a 60-second stall and a misleading "search failed".
        try:
            self.wait(PAGINATION_TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, RESULTS_ROW))
            )
        except TimeoutException:
            logger.info("[run %s] no result rows after grouping to Member Agency", self.run_id)

    def apply_sidebar_filters(self) -> None:
        """Narrow the current results page with the frontend's sidebar choices.

        Runs after the keyword search and the Member Agency grouping, so it
        filters that keyword's own result set — the search itself is untouched.
        A panel the portal did not render is reported into the run's errors and
        skipped: a partially-filtered run returns a superset of what was asked
        for, which is still usable, whereas aborting returns nothing.
        """
        self.set_step("applying_filters")
        report = SidebarDriver(self.driver, note=self._note_sidebar).apply(self.filters)
        # Report once per run — the filters are identical for every keyword, so
        # logging each pass would just be noise.
        if self._sidebar_report is None:
            self._sidebar_report = report
            run_manager.update_run(
                self.run_id,
                filters=self.filters.model_dump(exclude_none=True),
                filters_summary=self.filters.summary(),
                filters_applied=report,
            )
            logger.info("[run %s] sidebar filters applied: %s", self.run_id, report)

    def _note_sidebar(self, message: str) -> None:
        """Record a sidebar problem once per run, however many keywords hit it."""
        if message in self._sidebar_notes:
            return
        self._sidebar_notes.add(message)
        run_manager.add_error(self.run_id, message)

    def collect_links(self) -> list[str]:
        """Walk every results page, collecting solicitation detail links."""
        links: list[str] = []
        page_num = 1
        while True:
            rows = self.driver.find_elements(By.CSS_SELECTOR, "tr.mets-table-row a.solicitationsTitleLink")
            for row in rows:
                href = row.get_attribute("href")
                if href:
                    full = self._abs_url(href)
                    if full not in links:
                        links.append(full)
            logger.info("[run %s] collected links from page %s (total %s)", self.run_id, page_num, len(links))
            run_manager.update_run(self.run_id, bids_found=len(links))

            if page_num >= MAX_PAGES:
                break

            try:
                first_before = self.driver.find_element(
                    By.CSS_SELECTOR, "tr.mets-table-row a.solicitationsTitleLink"
                ).get_attribute("href")
            except WebDriverException:
                first_before = None

            next_button = self._find_next_button()
            if next_button is None:
                logger.info("[run %s] no further pages", self.run_id)
                break
            try:
                next_button.click()
            except WebDriverException as exc:
                logger.info("[run %s] could not click next page: %s", self.run_id, exc.__class__.__name__)
                break

            time.sleep(SEARCH_SETTLE_SECONDS)
            try:
                self.wait(PAGINATION_TIMEOUT).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr.mets-table-row"))
                )
            except TimeoutException:
                pass

            try:
                first_after = self.driver.find_element(
                    By.CSS_SELECTOR, "tr.mets-table-row a.solicitationsTitleLink"
                ).get_attribute("href")
            except WebDriverException:
                first_after = None
            if first_after == first_before:
                logger.info("[run %s] next page did not change results; stopping", self.run_id)
                break
            page_num += 1

        return links

    def _find_next_button(self):
        for sel in (
            "a.next.mets-pagination-page-icon:not(.disabled)",
            "a[rel='next']:not(.disabled)",
            "a.next:not(.disabled)",
        ):
            candidates = self.driver.find_elements(By.CSS_SELECTOR, sel)
            for candidate in candidates:
                try:
                    if candidate.is_displayed():
                        return candidate
                except WebDriverException:
                    continue
        return None

    def process_bid(self, link: str, dest_folder: Path) -> dict[str, Any] | None:
        """Open one solicitation, scrape its fields, and download its documents
        into a per-bid subfolder of `dest_folder`.

        Returns None when the solicitation is skipped by the close-date filter
        (closing sooner than MIN_DAYS_UNTIL_CLOSE) — checked before any document
        is fetched. Called once per distinct solicitation: the search phase has
        already deduplicated by link, so a bid found by several of the niche's
        keywords is never opened twice."""
        self.set_step("opening_bid")
        self.driver.get(link)
        try:
            self.wait(DETAIL_TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".mets-field"))
            )
        except TimeoutException:
            pass

        record: dict[str, Any] = {key: self._extract_field(label).strip() for key, label in DETAIL_FIELDS.items()}
        reference_number = record.get("reference_number") or ""
        title = record.get("title") or ""

        # Keep only solicitations still at least MIN_DAYS_UNTIL_CLOSE days from
        # their Closing Date, checked here — before the costly document download —
        # so a too-soon bid is skipped without fetching anything. An unreadable
        # closing date is kept and tallied. Returning None tells the caller to
        # drop this solicitation (and it is deliberately left out of the reuse
        # cache, so it never short-circuits the filter for a later keyword group).
        days_left = days_until_close(record.get("closing_date"))
        if days_left is None:
            self._kept_unreadable_close += 1
        elif days_left < MIN_DAYS_UNTIL_CLOSE:
            self._skipped_closing_soon += 1
            return None

        documents_count = self._document_count()
        record["documents_count"] = documents_count

        downloaded: list[str] = []
        if documents_count != "0":
            downloaded, _ = self._download_documents(reference_number, title, dest_folder)
        record["documents"] = downloaded
        return record

    def _document_count(self) -> str:
        try:
            tab = self.wait(DETAIL_TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#docs-itemsAbstractTab a"))
            )
            count = tab.find_element(By.CSS_SELECTOR, ".tabCount").text
            return count.strip() or "0"
        except WebDriverException as exc:
            logger.info("[run %s] doc count unavailable: %s", self.run_id, exc.__class__.__name__)
            return "0"

    def _download_documents(
        self, reference_number: str, title: str, dest_folder: Path
    ) -> tuple[list[str], Path | None]:
        """Download every document into a per-bid subfolder of `dest_folder`.
        Returns (saved filenames, the bid subfolder) — the folder is returned so a
        later group can copy the same files instead of re-downloading."""
        saved: list[str] = []
        try:
            self.driver.find_element(By.CSS_SELECTOR, "#docs-itemsAbstractTab a").click()
            time.sleep(SEARCH_SETTLE_SECONDS)
        except WebDriverException as exc:
            logger.info("[run %s] could not open docs tab for %s: %s", self.run_id, reference_number, exc.__class__.__name__)
            return saved, None

        bid_folder = dest_folder / f"{reference_number} - {_safe_title(title)}"
        bid_folder.mkdir(parents=True, exist_ok=True)

        buttons = self._download_buttons()
        logger.info("[run %s] %s download buttons for %s", self.run_id, len(buttons), reference_number)
        for index, button in enumerate(buttons):
            name = self._download_one(button, bid_folder, index)
            if name:
                saved.append(name)
        return saved, bid_folder

    def _download_buttons(self) -> list:
        found: list = []
        css = (
            "table tbody tr a[title*='Download'], "
            "table tbody tr a[title*='download'], "
            "table tbody tr a[href*='download']"
        )
        elements = self.driver.find_elements(By.CSS_SELECTOR, css)
        elements += self.driver.find_elements(
            By.XPATH, "//table//tbody//tr//a[contains(., 'Download')]"
        )
        for el in elements:
            if el not in found:
                found.append(el)
        return found

    def _download_one(self, button, bid_folder: Path, index: int) -> str | None:
        """Click a download link and move the resulting file into the bid folder.

        Falls back to a direct authenticated fetch of the href if the click does
        not produce a download (e.g. it opened an acknowledgement modal).
        """
        try:
            self.scroll_into_view(button)
            button.click()
            downloaded = self.wait_for_download(timeout=DOC_DOWNLOAD_TIMEOUT)
            target = self._move_into(downloaded, bid_folder, downloaded.name)
            logger.info("[run %s] downloaded %s", self.run_id, target.name)
            return target.name
        except (TimeoutException, WebDriverException) as exc:
            logger.info("[run %s] click download %s failed (%s); trying fallback", self.run_id, index, exc.__class__.__name__)
            try:
                self.driver.switch_to.active_element.send_keys(Keys.ESCAPE)
            except WebDriverException:
                pass
            return self._fallback_download(button, bid_folder, index)

    def _fallback_download(self, button, bid_folder: Path, index: int) -> str | None:
        try:
            href = button.get_attribute("href")
        except WebDriverException:
            href = None
        if not href or href.startswith("javascript"):
            logger.info("[run %s] no fallback href for document %s", self.run_id, index)
            return None

        href = self._abs_url(href)
        cookies = "; ".join(f"{c['name']}={c['value']}" for c in self.driver.get_cookies())
        request = urllib.request.Request(href, headers={"Cookie": cookies, "User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
                disposition = response.headers.get("Content-Disposition", "")
                filename = f"document_{index}.pdf"
                if "filename=" in disposition:
                    filename = disposition.split("filename=")[-1].split(";")[0].strip('"').strip("'")
                target = self._unique_path(bid_folder / filename)
                target.write_bytes(body)
                logger.info("[run %s] fallback downloaded %s", self.run_id, target.name)
                return target.name
        except Exception as exc:  # noqa: BLE001 — a single failed doc must not abort the bid
            logger.info("[run %s] fallback download %s failed: %s", self.run_id, index, exc)
            return None

    def _move_into(self, src: Path, folder: Path, name: str) -> Path:
        target = self._unique_path(folder / name)
        shutil.move(str(src), str(target))
        return target

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        stem, suffix = path.stem, path.suffix
        counter = 2
        while True:
            candidate = path.with_name(f"{stem} ({counter}){suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    # -- orchestration ------------------------------------------------------

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        self._save_run_row()
        try:
            self.start_driver()
            self.login()

            keywords = self.keywords
            logger.info("[run %s] niche %r: %s keyword(s), searched one at a time",
                        self.run_id, self.niche_label, len(keywords))

            # PHASE 1 — search every keyword of the niche in turn, collecting
            # solicitation links. Deduplicated by link across the whole run, so a
            # solicitation surfaced by five keywords is opened and downloaded
            # once; each link remembers every keyword that found it.
            link_keywords: dict[str, list[str]] = {}
            for index, keyword in enumerate(keywords, start=1):
                progress = f"{index}/{len(keywords)}"
                run_manager.update_run(
                    self.run_id, keyword=keyword, keyword_progress=progress
                )
                try:
                    # A long sequential run outlives BidNet's session, which
                    # surfaces as a redirect to the login page rather than as a
                    # timeout — no amount of waiting recovers it.
                    self.ensure_logged_in()
                    self.search(keyword)

                    # Fast-fail on an empty search. The group tab reports the hit
                    # count directly, so a keyword that matched nothing costs one
                    # search instead of a grouping click plus a full element wait
                    # for rows that never arrive. `None` means the count could
                    # not be read — carry on rather than risk skipping a keyword
                    # that does have bids.
                    count = self.result_count()
                    if count == 0:
                        logger.info(
                            "[run %s] No results found for keyword: %s. Moving to next keyword.",
                            self.run_id, keyword,
                        )
                        self._empty_keywords.append(keyword)
                        run_manager.update_run(
                            self.run_id, keywords_without_results=list(self._empty_keywords)
                        )
                        continue

                    self.filter_member_agency()
                    # Sidebar filters are re-applied per keyword: each search
                    # re-renders the panels, so the previous keyword's selection
                    # does not carry over.
                    self.apply_sidebar_filters()
                    links = self.collect_links()
                except StopRequested:
                    raise
                except (TimeoutException, WebDriverException) as exc:
                    # One bad keyword must not cost the other 20 — record it and
                    # carry on to the next search.
                    run_manager.add_error(
                        self.run_id, f"search failed for {keyword}: {exc.__class__.__name__}"
                    )
                    self.screenshot(f"search_{index}")
                    continue
                for link in links:
                    matched = link_keywords.setdefault(link, [])
                    if keyword not in matched:
                        matched.append(keyword)
                run_manager.update_run(self.run_id, bids_found=len(link_keywords))
                logger.info("[run %s] [%s] %s -> %s links (%s unique so far)",
                            self.run_id, progress, keyword, len(links), len(link_keywords))

            # PHASE 2 — open every distinct solicitation once and download its
            # documents into this run's single folder.
            self.set_step("collecting_bids")
            all_records: list[dict] = []
            for index, (link, matched) in enumerate(link_keywords.items()):
                record: dict[str, Any] | None = {
                    "reference_number": None, "title": None, "documents": [], "error": None,
                }
                try:
                    record = self.process_bid(link, self.document_folder)
                except StopRequested:
                    raise
                except (TimeoutException, WebDriverException) as exc:
                    record["error"] = str(exc)[:300]
                    run_manager.add_error(self.run_id, f"bid failed: {exc.__class__.__name__}")
                    self.screenshot(f"bid_{index}")
                if record is None:
                    # Skipped by the close-date filter — no documents fetched.
                    continue
                record["matched_keyword"] = ", ".join(matched)
                record["niche"] = self.niche_label
                run_manager.add_bid_result(self.run_id, record)
                all_records.append(record)

            logger.info("[run %s] %s unique solicitations from %s keyword search(es)",
                        self.run_id, len(link_keywords), len(keywords))

            # One master spreadsheet for the whole run, at the root of its folder.
            self._write_master_excel(all_records)

            if self._empty_keywords:
                logger.info(
                    "[run %s] %s of %s keyword(s) returned no bids: %s",
                    self.run_id, len(self._empty_keywords), len(keywords),
                    ", ".join(self._empty_keywords),
                )
                run_manager.add_warning(
                    self.run_id,
                    f"{len(self._empty_keywords)} of {len(keywords)} keywords matched "
                    f"nothing on BidNet: {', '.join(self._empty_keywords)}",
                )
            # Every keyword came back empty — the searches worked, the portal
            # simply has nothing for this niche right now.
            run_manager.update_run(
                self.run_id, no_results=len(self._empty_keywords) == len(keywords)
            )

            # Surface the close-date filter's effect (see app/core/closing_filter).
            run_manager.update_run(
                self.run_id,
                min_days_until_close=MIN_DAYS_UNTIL_CLOSE,
                bids_skipped_closing_soon=self._skipped_closing_soon,
                bids_kept_unreadable_close=self._kept_unreadable_close,
            )
            logger.info(
                "[run %s] close-date filter (≥%sd): kept %s, skipped %s closing soon, %s unreadable kept",
                self.run_id, MIN_DAYS_UNTIL_CLOSE, len(all_records),
                self._skipped_closing_soon, self._kept_unreadable_close,
            )

            # Persist every scraped solicitation in one transaction (mirrors
            # MyFlorida). The DB stays globally de-duplicated per run (by reference
            # number); the niche+tier split lives in the folders and their Excels.
            # Best-effort: a DB failure must not fail the run.
            run = run_manager.get_run(self.run_id) or {"run_id": self.run_id}
            try:
                stored = export.save_bids(run, all_records)
                run_manager.update_run(self.run_id, bids_stored_in_db=stored)
            except Exception:  # noqa: BLE001 — DB issues shouldn't abort the run
                logger.exception("[run %s] DB save failed", self.run_id)
                run_manager.add_error(self.run_id, "db save failed (see logs)")

            run_manager.update_run(self.run_id, excel_exported=True)

            # Package the run into one archive ZIP — the cumulative run-level
            # Excel (from the DB) at the root plus every niche+tier group folder
            # with its own Excel and documents — then delete the workspace.
            self.set_step("packaging_results")
            archive_run(self.run_id)

            run_manager.update_run(self.run_id, status="completed", step="done")
            # Email/S3 notification on successful completion.
            notify_scrape_completion(self.run_id, "bidnet", len(all_records))
        except Exception as exc:  # noqa: BLE001 — a failed run must be reported, not crash the worker
            logger.exception("[run %s] failed", self.run_id)
            self.screenshot("fatal")
            run_manager.add_error(self.run_id, str(exc)[:500])
            run_manager.update_run(self.run_id, status="failed", step="failed")
        finally:
            self.cleanup()
            run_manager.update_run(self.run_id, finished_at=datetime.now().isoformat())
            self._save_run_row()
            run_manager.remove_empty_folder(self.run_id)

    def _write_master_excel(self, records: list[dict]) -> None:
        """One master spreadsheet for the run, at the root of its folder.

        Every keyword's bids in a single sheet — one row per solicitation, with
        every keyword that surfaced it comma-joined in `Matched Keyword`.

        Named with `exports.excel_name`, which is also what the packaging step
        calls its DB-regenerated copy: matching names mean `build_zip` recognises
        this file as already present and does not add a second spreadsheet. So
        the ZIP ships exactly one Excel — the DB's version when the database is
        reachable, this one when it is not.
        """
        self.set_step("generating_excel")
        run = run_manager.get_run(self.run_id) or {"run_id": self.run_id, "scraper": "bidnet"}
        out_path = self._unique_path(self.document_folder / excel_name(run))
        try:
            export.generate_excel_from_records(records, out_path)
            run_manager.update_run(self.run_id, excel_path=str(out_path))
            logger.info("[run %s] wrote %s bids to %s", self.run_id, len(records), out_path.name)
        except Exception:  # noqa: BLE001 — never fail a whole run over the spreadsheet
            logger.exception("[run %s] master Excel generation failed", self.run_id)
            run_manager.add_error(self.run_id, "excel generation failed (see logs)")

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
    niche_key: str,
    filters: SidebarFilterRequest | None = None,
) -> None:
    """Run one niche: resolve its keywords from the database, then search each
    in turn. Resolved here rather than passed in so the run always uses the
    catalog as it stands when the browser actually starts."""
    session = SessionLocal()
    try:
        niche = niches.get_niche(session, niche_key)
        keywords = niches.keywords_for(session, niche_key)
        label = niche.label if niche else niche_key
    finally:
        session.close()

    if not keywords:
        run_manager.add_error(run_id, f"niche '{label}' has no keywords to search")
        run_manager.update_run(run_id, status="failed", step="failed")
        return

    BidnetScraper(run_id, keywords, filters, niche_label=label).run()
