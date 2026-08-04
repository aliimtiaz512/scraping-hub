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
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.config import settings
from app.core import run_manager
from app.core.base_scraper import BaseScraper, StopRequested
from app.core.closing_filter import MIN_DAYS_UNTIL_CLOSE, days_until_close
from app.core.filenames import sanitize_filename
from app.db import SessionLocal
from app.scrapers.bidnet import documents, export, niches, storage
from app.scrapers.bidnet.filters import SidebarFilterRequest
from app.scrapers.bidnet.sidebar import SidebarDriver
from app.core.exports import archive_run
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
SEARCH_SETTLE_SECONDS = 5  # after switching result group / paging results
# Attachment detection and download live in `documents.py`, which waits on the
# documents tab's lazily-rendered links rather than sleeping a fixed interval,
# and fetches the files over HTTP instead of clicking them one at a time.
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

# How complete a scraped record is. Every bid the run opens is kept and exported
# under one of these, so a solicitation is never silently absent from the output.
STATUS_OK = "OK"                              # every expected field was read
STATUS_PARTIAL = "PARTIAL_DATA"               # some fields read, key ones missing
STATUS_FAILED = "EXTRACTION_FAILED"           # detail page yielded nothing at all
# The portal never served the bid: it redirected to a "required acknowledgement"
# page that has to be Accepted first. Distinct from EXTRACTION_FAILED because
# nothing is broken — the bid is gated, and no amount of retrying opens it.
STATUS_ACK_REQUIRED = "ACKNOWLEDGEMENT_REQUIRED"
RECORD_STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_FAILED, STATUS_ACK_REQUIRED)

# The acknowledgement interstitial. Requesting a gated solicitation redirects to
# `/private/supplier/solicitations/<id>/req-ack`, which asks the vendor to
# Accept or Decline something — an attestation ("Company must be based in the
# United States of America"), or confirmation that an addendum was read.
#
# It defeats a naive detail scrape twice over: the page *does* contain
# `.mets-field` elements (the acknowledgement's own), so waiting for that
# selector succeeds, and every solicitation label is simply absent, so all
# fields come back "" and the record used to be filed as EXTRACTION_FAILED —
# then retried, onto the identical wall.
ACK_URL_MARKER = "/req-ack"
ACK_ACCEPT_BUTTON = "#requiredAcknowledgementConfirmPage"
ACK_DECLINE_BUTTON = "#requiredAcknowledgementDeclinePage"
ACK_NAME = ".acknowledgementName"
ACK_MESSAGE = ".noWidthAcknowledgementMessage"
# The Accept button is a jQuery `commandButton` of type="submit" inside
# `<form name="solicitationForm" method="POST">`, which carries a `_csrf`
# hidden input. So it must be *clicked* — posting the form by hand, or
# navigating anywhere, loses the token and the acknowledgement is not recorded.
ACK_FORM = "form[name='solicitationForm']"
# The cookie banner renders over the dialog's button bar on a fresh session and
# will swallow the click ("element click intercepted"), which reads as a failed
# acceptance. Dismissed once before the first Accept.
COOKIE_ACCEPT_BUTTON = "#cookieBannerAcceptBtn"
# A solicitation can stack several acknowledgements — accepting one lands on the
# next rather than on the bid. Bounded so a page that never clears cannot spin.
MAX_ACK_ACCEPTS = 5

# Fields that must be present for a record to count as fully scraped. A record
# missing these is still exported — flagged, with its detail URL — rather than
# dropped, because a bid we failed to read is exactly the one worth chasing.
REQUIRED_FIELDS = ("reference_number", "title")

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
        # This run's niche folder inside the day's session root, and the
        # `documents/` folder within it that every bid's attachments land in
        # (per-bid subfolders — see storage.py). The spreadsheet sits beside
        # `documents/`, at the root of the niche folder.
        run = run_manager.get_run(run_id) or {}
        folder = run.get("folder") or str(
            storage.niche_folder(storage.session_root(), self.niche_label)
        )
        self.niche_folder = Path(folder)
        self.niche_folder.mkdir(parents=True, exist_ok=True)
        self.document_folder = storage.documents_folder(self.niche_folder)
        self.niche_key = run.get("niche") or ""
        self.niche_slug = run.get("niche_slug")
        # Keywords the portal reported zero bids for — skipped without waiting
        # on rows that were never coming, and surfaced on the run so a niche
        # whose terms all miss is obvious rather than silent.
        self._empty_keywords: list[str] = []
        # Close-date filter tallies (see app/core/closing_filter): solicitations
        # dropped for closing too soon (before their documents are downloaded),
        # and those kept despite an unreadable Closing Date.
        self._skipped_closing_soon = 0
        self._kept_unreadable_close = 0
        # Document tallies for the run summary: how many attachments were
        # detected across every bid, how many actually landed on disk, and how
        # many bids had a tab badge that disagreed with the links found. A run
        # where detected and downloaded diverge is the signal that documents are
        # being lost, which the old code could not report at all.
        self._documents_detected = 0
        self._documents_downloaded = 0
        self._documents_failed = 0
        self._documents_duplicates = 0
        self._doc_count_mismatches = 0
        # Solicitations the portal gated behind a required acknowledgement.
        # Collected so the run can list exactly which bids need a human Accept
        # rather than burying them among genuine extraction failures.
        self._acknowledgement_required: list[dict[str, str]] = []
        # Acknowledgements this run accepted on the account's behalf. Recorded
        # and reported because each one is a submission the issuing agency can
        # see — a run should never accept things without saying which.
        self._accepted_acknowledgements: list[dict[str, str]] = []
        # One pooled HTTP session for the whole run, cookies refreshed per bid.
        self._session: requests.Session | None = None

    # -- helpers ------------------------------------------------------------

    def _extract_field(self, field_name: str, link: str = "") -> str:
        """Read a .mets-field body paragraph whose field contains the label.

        Returns "" when the field is absent, logged with the solicitation it
        belongs to — a bare "failed to extract Title" says nothing about which
        bid is short a title.
        """
        xpath = (
            f"//div[contains(@class,'mets-field')][contains(., \"{field_name}\")]"
            f"//div[contains(@class,'mets-field-body')]//p"
        )
        try:
            return self.driver.find_element(By.XPATH, xpath).text
        except WebDriverException as exc:
            logger.info(
                "[run %s] field %r not on the page (%s)%s",
                self.run_id, field_name, exc.__class__.__name__,
                f" for {link}" if link else "",
            )
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
        record = self._scrape_detail(link)

        # Checked before the retry: a gated bid is not a failed read, and
        # reloading only lands on the same acknowledgement page again — which is
        # exactly how one blocked solicitation used to cost three page loads and
        # still be filed as EXTRACTION_FAILED.
        gate = self._acknowledgement_gate()
        if gate:
            if settings.bidnet_auto_accept_acknowledgements:
                accepted, gate = self._clear_acknowledgements(link)
                if gate is None:
                    # Through the wall — carry on with the bid we just read.
                    record = accepted or record
            if gate is not None:
                return self._gated_record(link, gate)

        # A detail page that renders nothing used to be indistinguishable from a
        # solicitation with no data: every field came back "" and the blank
        # record flowed on as if it were real. Retry once — a single slow load is
        # the common cause — then flag whatever we end up with.
        if not any(record.values()):
            logger.warning(
                "[run %s] no fields could be read from %s — reloading once", self.run_id, link
            )
            record = self._scrape_detail(link)
            gate = self._acknowledgement_gate()
            if gate:
                return self._gated_record(link, gate)

        record["detail_url"] = link
        record["status"] = self._classify(record)
        if record["status"] != STATUS_OK:
            missing = [f for f in DETAIL_FIELDS if not record.get(f)]
            message = (
                f"{record['status']} for {link} — could not read: {', '.join(missing)}"
            )
            logger.warning("[run %s] %s", self.run_id, message)
            run_manager.add_error(self.run_id, message)
            record["error"] = message

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

        # Detection is never gated on the tab's count badge: an unreadable badge
        # used to be recorded as "0 documents" and the bid's attachments were
        # then never looked for at all. We always open the documents tab and
        # wait for the attachment anchors themselves; the badge is only a
        # cross-check to log against.
        outcome = self._collect_documents(reference_number, title, dest_folder, link)
        record["documents_count"] = str(outcome.distinct)
        record["documents"] = outcome.saved
        record["documents_downloaded"] = outcome.downloaded
        if outcome.failed:
            record["documents_failed"] = outcome.failed
        return record

    def _scrape_detail(self, link: str) -> dict[str, Any]:
        """Load a solicitation's detail page and read its fields.

        A load timeout is logged with the link rather than swallowed: it is the
        one signal that the fields about to come back empty are a failure and not
        an empty solicitation.
        """
        self.driver.get(link)
        try:
            self.wait(DETAIL_TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".mets-field"))
            )
        except TimeoutException:
            logger.warning(
                "[run %s] detail page did not render within %ss: %s",
                self.run_id, DETAIL_TIMEOUT, link,
            )
        return {key: self._extract_field(label, link).strip() for key, label in DETAIL_FIELDS.items()}

    def _gated_record(self, link: str, gate: dict[str, str]) -> dict[str, Any]:
        """A record for a bid the portal will not show until it is acknowledged.

        Exported like any other row — flagged, with its detail URL and what the
        portal is asking — because a bid we are *eligible for but blocked on* is
        one worth chasing by hand, not one to drop. Its title falls back to the
        heading the acknowledgement page still shows, so the row is
        identifiable in the spreadsheet rather than a URL and seven blanks.
        """
        ask = gate.get("name") or "a required acknowledgement"
        message = (
            f"{STATUS_ACK_REQUIRED} for {link} — the portal requires "
            f"\"{ask}\" to be accepted before this bid can be read"
        )
        logger.warning("[run %s] %s", self.run_id, message)
        run_manager.add_warning(self.run_id, message)
        self._acknowledgement_required.append({"url": link, "name": ask})

        record: dict[str, Any] = {key: "" for key in DETAIL_FIELDS}
        record["title"] = self._page_heading() or ask
        record["detail_url"] = link
        record["status"] = STATUS_ACK_REQUIRED
        record["error"] = message
        record["acknowledgement"] = {k: v for k, v in gate.items() if v}
        # Documents sit behind the same wall — nothing to count or fetch.
        record["documents_count"] = "0"
        record["documents"] = []
        record["documents_downloaded"] = 0
        return record

    def _page_heading(self) -> str:
        """The solicitation heading the acknowledgement page still renders."""
        try:
            for element in self.driver.find_elements(By.CSS_SELECTOR, "h1, h2"):
                text = (element.text or "").strip()
                if text:
                    return text
        except WebDriverException:
            pass
        return ""

    def _acknowledgement_gate(self) -> dict[str, str] | None:
        """The acknowledgement blocking the current page, or None.

        Keyed on the `/req-ack` redirect plus the Accept button, so an ordinary
        detail page that merely mentions the word is never mistaken for one.
        Returns what the portal is asking, so the run can say *why* a bid could
        not be read instead of just that it could not.
        """
        try:
            url = self.driver.current_url or ""
            has_button = bool(self.driver.find_elements(By.CSS_SELECTOR, ACK_ACCEPT_BUTTON))
        except WebDriverException:
            return None
        if ACK_URL_MARKER not in url and not has_button:
            return None

        def text(selector: str) -> str:
            try:
                found = self.driver.find_elements(By.CSS_SELECTOR, selector)
                return found[0].text.strip() if found else ""
            except WebDriverException:
                return ""

        return {
            "url": url,
            "name": text(ACK_NAME),
            "message": text(ACK_MESSAGE),
        }

    def _dismiss_cookie_banner(self) -> None:
        """Close the cookie banner if it is up.

        It renders over the acknowledgement dialog's button bar on a fresh
        session, so the Accept click lands on the banner instead and the
        acknowledgement is silently never submitted.
        """
        try:
            for button in self.driver.find_elements(By.CSS_SELECTOR, COOKIE_ACCEPT_BUTTON):
                if button.is_displayed():
                    self.driver.execute_script("arguments[0].click();", button)
                    logger.info("[run %s] dismissed the cookie banner", self.run_id)
                    return
        except WebDriverException:
            pass

    def _accept_acknowledgement(self) -> bool:
        """Click Accept on the acknowledgement page.

        The button is a jQuery `commandButton` of `type="submit"` inside the
        CSRF-protected `solicitationForm`, so the acceptance has to go through a
        real click on that element — posting the form by hand would drop the
        token. A native Selenium click is tried first (closest to a user), with
        a JS click as the fallback for when something still overlays it.

        Returns True only once the page has actually left the acknowledgement,
        so a click that was swallowed is reported as a failure rather than
        assumed to have worked.
        """
        self._dismiss_cookie_banner()
        try:
            button = self.wait(DETAIL_TIMEOUT).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, ACK_ACCEPT_BUTTON))
            )
            self.scroll_into_view(button)
        except (TimeoutException, WebDriverException) as exc:
            logger.warning(
                "[run %s] no Accept button to click: %s", self.run_id, exc.__class__.__name__
            )
            return False

        try:
            button.click()
        except WebDriverException as exc:
            logger.info(
                "[run %s] native Accept click failed (%s) — retrying via JS",
                self.run_id, exc.__class__.__name__,
            )
            try:
                self.driver.execute_script("arguments[0].click();", button)
            except WebDriverException:
                logger.warning("[run %s] could not click Accept at all", self.run_id)
                return False

        # The submit navigates; wait for this acknowledgement to go away rather
        # than sleeping. Landing on the *next* acknowledgement also counts as
        # progress — the caller loops.
        try:
            self.wait(DETAIL_TIMEOUT).until(EC.staleness_of(button))
        except TimeoutException:
            logger.warning(
                "[run %s] the acknowledgement page did not change after Accept", self.run_id
            )
            return False
        self._await_ajax_idle()
        return True

    def _clear_acknowledgements(self, link: str) -> tuple[dict[str, Any], dict[str, str] | None]:
        """Accept each acknowledgement gating `link`, then read the bid.

        A solicitation can stack more than one (a pass/fail requirement *and* a
        US-based-company attestation), so accepting once can simply land on the
        next. Returns the scraped record and the acknowledgement still blocking
        it, or None once the bid itself is readable.
        """
        record: dict[str, Any] = {}
        gate = self._acknowledgement_gate()
        for attempt in range(MAX_ACK_ACCEPTS):
            if gate is None:
                break
            logger.info(
                "[run %s] accepting required acknowledgement %r (%d of at most %d) for %s",
                self.run_id, gate.get("name") or "(unnamed)", attempt + 1, MAX_ACK_ACCEPTS, link,
            )
            if not self._accept_acknowledgement():
                self.screenshot(f"ack_{sanitize_filename(gate.get('name') or 'unnamed')[:40]}")
                return record, gate
            self._accepted_acknowledgements.append(
                {"url": link, "name": gate.get("name") or "(unnamed)"}
            )
            # Re-read the page we landed on: either the bid, or the next
            # acknowledgement in the stack.
            record = self._read_current_detail(link)
            gate = self._acknowledgement_gate()
        if gate is not None:
            logger.warning(
                "[run %s] still gated after %d acceptance(s): %s",
                self.run_id, MAX_ACK_ACCEPTS, link,
            )
        return record, gate

    def _read_current_detail(self, link: str) -> dict[str, Any]:
        """Read the detail fields from the page already loaded.

        Used straight after an Accept: the portal has navigated to the bid
        itself, and re-requesting `link` would be a wasted round trip.
        """
        try:
            self.wait(DETAIL_TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".mets-field"))
            )
        except TimeoutException:
            logger.warning(
                "[run %s] no fields rendered after accepting for %s", self.run_id, link
            )
        return {key: self._extract_field(label, link).strip() for key, label in DETAIL_FIELDS.items()}

    @staticmethod
    def _classify(record: dict[str, Any]) -> str:
        """OK / PARTIAL_DATA / EXTRACTION_FAILED for a scraped record."""
        if not any(record.get(field) for field in DETAIL_FIELDS):
            return STATUS_FAILED
        if not all(record.get(field) for field in REQUIRED_FIELDS):
            return STATUS_PARTIAL
        return STATUS_OK

    def _collect_documents(
        self, reference_number: str, title: str, dest_folder: Path, detail_url: str
    ) -> documents.DownloadOutcome:
        """Detect and download every attachment for the solicitation on screen.

        Detection opens the lazily-rendered documents tab and waits for the
        attachment anchors; downloading then fetches those hrefs directly over
        HTTP, in parallel and streamed to disk, rather than clicking each link
        and waiting on Chrome's download directory one file at a time.
        """
        label = reference_number or detail_url
        links, badge = documents.extract_document_links(
            self.driver, self.run_id, label, detail_url
        )

        expected = int(badge) if badge is not None and badge.isdigit() else None

        if not links:
            if expected:
                # Detected-but-unreachable is the case that must never pass
                # silently: the badge says there are files and we could not
                # reach a single one.
                message = (
                    f"documents tab reports {expected} file(s) for {label} but no "
                    f"download links could be found"
                )
                logger.error("[run %s] %s", self.run_id, message)
                run_manager.add_error(self.run_id, message)
                self.screenshot(f"no_docs_{sanitize_filename(label)[:40]}")
            logger.info("[run %s] Found 0 documents for %s", self.run_id, label)
            return documents.DownloadOutcome(detected=0, badge=badge)

        bid_folder = dest_folder / f"{reference_number} - {_safe_title(title)}"
        outcome = documents.download_documents(
            self._http_session(),
            links,
            bid_folder,
            self.run_id,
            label,
            referer=detail_url,
            should_stop=self.raise_if_stopped,
        )
        outcome.badge = badge
        self._documents_detected += outcome.distinct
        self._documents_downloaded += outcome.downloaded
        self._documents_duplicates += outcome.duplicates

        # Reconcile against the badge only now — after the duplicates the portal
        # lists twice have been collapsed. Comparing the raw link count would
        # flag a bid whose documents are all present as a mismatch.
        if expected is not None and expected != outcome.distinct:
            self._doc_count_mismatches += 1
            logger.warning(
                "[run %s] %s: documents tab reports %d file(s) but %d distinct "
                "document(s) were found",
                self.run_id, label, expected, outcome.distinct,
            )

        if outcome.failed:
            self._documents_failed += len(outcome.failed)
            run_manager.add_error(
                self.run_id,
                f"{len(outcome.failed)} of {outcome.detected} document(s) failed to "
                f"download for {label}",
            )
        return outcome

    def _http_session(self) -> requests.Session:
        """The run's shared HTTP session, cookies refreshed from the browser.

        One session for the whole run keeps connections pooled; the cookies are
        re-copied per bid because BidNet rotates the session cookie and a stale
        one turns every download into a login-page redirect.
        """
        self._session = documents.build_session(self.driver, self._session)
        return self._session

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

            # Reconciliation, printed before anything is saved: every collected
            # solicitation is accounted for, and the export count is the sum of
            # the parts. If these ever disagree, the run says so here rather than
            # leaving it to be noticed in the spreadsheet days later.
            by_status = Counter(r.get("status") or STATUS_OK for r in all_records)
            fallback = by_status[STATUS_PARTIAL] + by_status[STATUS_FAILED]
            logger.info(
                "[SUMMARY] run %s | Scraped: %d | Fully extracted: %d | Failed/Fallback: %d "
                "| Acknowledgement required: %d | Final Export Count: %d "
                "| Skipped (closing soon): %d",
                self.run_id, len(link_keywords), by_status[STATUS_OK], fallback,
                by_status[STATUS_ACK_REQUIRED], len(all_records),
                self._skipped_closing_soon,
            )
            expected = len(link_keywords) - self._skipped_closing_soon
            if len(all_records) != expected:
                logger.error(
                    "[SUMMARY] run %s | MISMATCH: %d collected - %d skipped = %d expected, "
                    "but %d record(s) are being exported",
                    self.run_id, len(link_keywords), self._skipped_closing_soon,
                    expected, len(all_records),
                )
            run_manager.update_run(
                self.run_id,
                bids_fully_extracted=by_status[STATUS_OK],
                bids_partial=by_status[STATUS_PARTIAL],
                bids_extraction_failed=by_status[STATUS_FAILED],
                bids_acknowledgement_required=by_status[STATUS_ACK_REQUIRED],
                acknowledgements_required=self._acknowledgement_required,
                acknowledgements_accepted=self._accepted_acknowledgements,
            )

            # Say plainly what was accepted on the account's behalf. Each one is
            # a submission the issuing agency can see, so it belongs in the run
            # record rather than only in a log line.
            if self._accepted_acknowledgements:
                logger.info(
                    "[run %s] accepted %d required acknowledgement(s): %s",
                    self.run_id, len(self._accepted_acknowledgements),
                    "; ".join(
                        f"{item['name']} → {item['url']}"
                        for item in self._accepted_acknowledgements
                    ),
                )
                run_manager.add_warning(
                    self.run_id,
                    f"accepted {len(self._accepted_acknowledgements)} required "
                    f"acknowledgement(s) on BidNet to read gated solicitations: "
                    + ", ".join(
                        sorted({item["name"] for item in self._accepted_acknowledgements})
                    ),
                )

            # Gated bids get their own line: they are not scrape failures, and
            # the only thing that opens them is a human clicking Accept on the
            # portal (or enabling bidnet_auto_accept_acknowledgements).
            if self._acknowledgement_required:
                listing = "; ".join(
                    f"{item['name']} → {item['url']}" for item in self._acknowledgement_required
                )
                logger.warning(
                    "[run %s] %d solicitation(s) are gated behind a required "
                    "acknowledgement and could not be read: %s",
                    self.run_id, len(self._acknowledgement_required), listing,
                )
                run_manager.add_warning(
                    self.run_id,
                    f"{len(self._acknowledgement_required)} solicitation(s) need a "
                    f"required acknowledgement accepted on BidNet before they can be "
                    f"read — they are in the spreadsheet flagged "
                    f"{STATUS_ACK_REQUIRED} with their detail URLs",
                )

            # Documents, reconciled the same way the bids are: detected versus
            # actually saved. Anything short here means a bid's folder is
            # missing a file its spreadsheet row claims, which is worth an
            # error rather than a line buried in the log.
            logger.info(
                "[DOCUMENTS] run %s | Detected: %d | Downloaded: %d | Failed: %d | "
                "Duplicate copies removed: %d | Count mismatches: %d",
                self.run_id, self._documents_detected, self._documents_downloaded,
                self._documents_failed, self._documents_duplicates,
                self._doc_count_mismatches,
            )
            run_manager.update_run(
                self.run_id,
                documents_detected=self._documents_detected,
                documents_downloaded=self._documents_downloaded,
                documents_failed=self._documents_failed,
            )
            if self._documents_failed:
                run_manager.add_warning(
                    self.run_id,
                    f"{self._documents_failed} of {self._documents_detected} detected "
                    f"document(s) could not be downloaded",
                )

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
            if self._session is not None:
                self._session.close()
                self._session = None
            self.cleanup()
            run_manager.update_run(self.run_id, finished_at=datetime.now().isoformat())
            self._save_run_row()
            run_manager.remove_empty_folder(self.run_id)

    def _write_master_excel(self, records: list[dict]) -> None:
        """This niche's spreadsheet, at the root of its niche folder.

        Every keyword's bids in a single sheet — one row per solicitation, with
        every keyword that surfaced it comma-joined in `Matched Keyword`.

        Named `<Niche>_Bids.xlsx`, which is also the name the packaging step
        writes its DB-regenerated copy to. Same path, so the archive ships one
        spreadsheet per niche — the DB's version (covering every run of this
        niche today) when the database is reachable, this one when it is not.
        It is deliberately *not* uniquified: a re-run of the same niche should
        refresh its sheet rather than leave a second one beside it.
        """
        self.set_step("generating_excel")
        out_path = storage.excel_path(
            self.niche_folder, self.niche_label, self.niche_key, self.niche_slug
        )
        try:
            written = export.generate_excel_from_records(records, out_path)
            run_manager.update_run(self.run_id, excel_path=str(out_path))
            # The writer's own count, never len(records): logging the input count
            # is what let a filtered-out row look exported.
            logger.info("[run %s] wrote %s bids to %s", self.run_id, written, out_path.name)
            if written != len(records):
                logger.error(
                    "[run %s] spreadsheet holds %s row(s) but %s record(s) were collected — "
                    "records were dropped by the writer",
                    self.run_id, written, len(records),
                )
                run_manager.add_error(
                    self.run_id,
                    f"spreadsheet holds {written} rows but {len(records)} bids were collected",
                )
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
