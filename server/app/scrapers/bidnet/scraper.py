"""Selenium automation for the BidNet Direct vendor portal.

A run searches **one niche**, and every term that niche owns is searched
**separately, one at a time, in a single browser session** — never combined into
a boolean query. A combined `A AND B` search only returns solicitations matching
both terms, a small fraction of what the terms find individually; searching them
in sequence is what makes the bid count whole. Terms come from the database
(see `niches.py`); the frontend only ever sends a niche key.

A niche owns **two kinds of term**, and both go into the same search box: its
keywords ("logo design"), then its NIGP class-item / UNSPSC codes ("965-46").
They are one queue — `SearchTerm` carries the kind, which the logs name and the
export records — because to the portal they are the same thing, text typed into
`#solicitationSingleBoxSearch`. Codes are searched *after* every keyword so a
bid's `Matched Keyword` column leads with the human-readable term that found it.

The same solicitation turning up under several terms is the normal case, not the
exception — that is what searching twenty-odd terms in one sector produces, and
searching codes on top of keywords produces more of it. It is opened, downloaded
and exported **once**: see `_bid_key` and `_seen_bid_ids`.

Flow, per run:

    login
    open_filtered_session                   <- ONCE: sidebar filters incl. the
                                               Published/Closing Date panels
    for each term of the niche:             <- keywords, then NIGP codes; one
                                               queue, sequential, same session
        ensure_logged_in                    <- re-login if the session expired
        search(term)                        <- typed into the box in place; the
                                               session's filters ride along
        result_count() == 0 ?  -> skip      <- fast-fail, no waiting on empty results
        filter to "Member Agency Bids"
        confirm_filters_active              <- read-only; re-applies only on drift
        paginate, collecting solicitation links
    for each distinct solicitation:         <- deduplicated across every term
        open it, scrape its fields                 <- metadata only, no downloads
        seen already?          -> skip      <- second dedup, on the reference no.
    write one master Excel, persist to the DB, package the run

**The sidebar filters are applied once per run, not once per keyword.** They
belong to the search form rather than to a keyword's results, so a new keyword
typed into the box is searched *with them already in force* — which is both
faster (one set of filter postbacks per run instead of one per keyword, on a
portal where each is a full page round-trip) and safer: there is no longer a
window in which a keyword's results are read before its date window landed.

The cost of keeping them is that the page is no longer reloaded between
keywords — a reload is what used to clear them. So the two things the reload was
also doing are done explicitly instead: the keyword box is cleared through the
DOM before the next term is typed (`search`), and the results are put back on
page 1 before they are harvested (`_ensure_first_result_page`). Anything that
does navigate — a re-login, a keyword that failed mid-page — marks the filters
lost, and the next keyword re-establishes them before it searches.

A run is **metadata only**. Attachment harvesting — the documents tab, the
per-bid `<Reference> - <Title>/` folders and the HTTP downloads that filled
them — is gone (see `DOWNLOAD_DOCUMENTS` below), so a solicitation costs exactly
one page load and a niche folder holds exactly one file: its spreadsheet.

Three ways to execute it, differing only in what goes into the search box and
what comes out the other end:

* **One niche** (this module) — the flow above, writing `<Niche>_Bids.xlsx`
  into the day's shared session root.
* **Run all niches** (`batch.py`) — one such run per niche in the catalog,
  bundled as a single ZIP of per-niche spreadsheets.
* **Run all member agency bids** (`member_agencies.py`) — no search terms at
  all: the sidebar filters alone, the Member Agency Bids group, and every page
  of it into one consolidated sheet.
"""

import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

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
from app.scrapers.bidnet import export, niches, selectors, storage
from app.scrapers.bidnet.filters import SidebarFilterRequest
from app.scrapers.bidnet.niches import KIND_NIGP, SearchTerm
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
DETAIL_TIMEOUT = 45        # solicitation detail page
SEARCH_SETTLE_SECONDS = 5  # after switching result group / paging results
MAX_PAGES = 100  # pagination safety guard, same as the original

# How many times a single page transition is retried before the walk gives up.
# A next-page click that does not land is nearly always the grid still settling,
# and one transient miss used to cost every page after it.
PAGE_ADVANCE_ATTEMPTS = 3

# Rows per results page, set through the grid's own "Results per page" dropdown
# before anything is harvested.
#
# The portal defaults to 25, and 25 is what made the member-agency sweep a
# 74-page walk that lost two thirds of its bids somewhere in the middle. Every
# page transition is a postback that can be intercepted, go stale, or simply not
# render, so the cheapest way to collect a large list reliably is to ask for
# fewer transitions: 100 turns 1,850 bids from 74 pages into 19.
#
# 100 is the largest the dropdown offers (25 / 50 / 100).
PAGE_SIZE = 100

# The shared close-date rule (app/core/closing_filter): keep only solicitations
# still at least MIN_DAYS_UNTIL_CLOSE days from closing. **Off for the testing
# phase** — a run collects every active opportunity the portal returns, whatever
# its closing date, so what the scraper reports can be compared against what a
# manual search shows without a date window in between.
#
# Switched rather than deleted: the rule, its tallies and its reporting are all
# still here, and flipping this back to True restores them with no other change.
APPLY_CLOSE_DATE_FILTER = False

# ---------------------------------------------------------------------------
# Testing-phase switches. Three lines, each independently revertible.
# ---------------------------------------------------------------------------

# Show the browser and slow the flow down so form interactions, date-picker
# overlays and page reloads can be watched as they happen.
#
# **On (True) is production**, and that is where this now sits: the filter logic
# it was turned off to debug is verified, so runs are headless again and every
# `[LIVE DEBUG]` line and pacing pause below is silent, because both hang off
# this flag alone.
#
# Per-run visibility does not need this switch. The console's "Show browser"
# toggle sets `live_preview` on the run and `BaseScraper.start_driver` shows the
# window for that run alone; this flag is the blunt always-on version, for a
# debugging session where every run should be watched. Turning it back to False
# overrides the per-run flag for *every* run, which is why it does not belong on
# in production.
HEADLESS_MODE = True

# Seconds to pause after a form interaction while HEADLESS_MODE is False, so a
# watched step is readable rather than a flicker. Zero cost when headless.
DEBUG_PAUSE_SECONDS = 1.5

# Apply the sidebar's Published/Closing Date panels at all. Off skips them
# entirely and searches on keyword + status alone — the fastest way to establish
# whether a date panel is what is emptying a result set, since it removes the
# panel from the run rather than trying to read its state. The panels themselves
# are untouched; flipping this back to True restores them.
APPLY_DATE_FILTERS = True

# ---------------------------------------------------------------------------
# Attachment harvesting — retired.
# ---------------------------------------------------------------------------
#
# A run reads solicitation **metadata only**. It no longer opens the documents
# tab, no longer detects attachment links, no longer creates the per-bid
# `<Reference> - <Title>/` folder each bid's files used to land in, and no
# longer fetches a single `.pdf` / `.docx` / `.xlsx` / addendum.
#
# Why it went: attachments were the entire cost of a run and none of its value.
# A bid cost one detail page load plus a documents-tab render plus one HTTP
# fetch per file, and a niche of a few hundred bids could pull gigabytes into
# the workspace on its way to a ZIP nobody opened the documents in. The
# deliverable is, and now only is, the spreadsheet — every column of which comes
# off the detail page itself. Dropping the downloads takes a solicitation down
# to a single page load and a niche folder down to a single file.
#
# **Counting them is a separate question, and the answer there is yes** — see
# `count_documents` on the scraper and `_count_documents` below. A member agency
# sweep reports how many attachments each solicitation carries, which is a
# useful triage signal (a bid with fourteen documents is a different proposition
# from one with none) and costs a tab render rather than a file transfer. What
# stays gone is fetching the bytes and the per-bid folder that held them.
#
# This is a constant rather than a setting because it is not a choice a run
# makes — nothing in the pipeline (packaging, email, the console) expects
# document *files* any more.
DOWNLOAD_DOCUMENTS = False

# How long to wait for the documents tab's AJAX to put the attachment anchors in
# the DOM, once the tab has been opened. Lower than the 30s the download path
# used: a sweep pays this per bid across a couple of thousand of them, and a tab
# that has not rendered in this long is not about to.
DOC_TAB_TIMEOUT = 15
# Confirmation wait for a bid whose badge positively reads "0". Short, because
# there is nothing to wait for — but not skipped, so a mis-rendered badge still
# gets one look at the tab body.
DOC_ZERO_TIMEOUT = 3
# Poll interval while waiting for those anchors.
DOC_POLL = 0.25

# The result-group tab the scraper scrapes ("Member Agency Bids"). Its header
# carries the authoritative hit count for the current search:
#
#   <div class="searchContentGroupContainer" search-content-group-id="2085061601">
#     <span class="solicitationCount">1,848</span> … Member Agency Bids
#
# which is what lets a zero-result keyword be skipped without waiting on rows
# that are never coming. See `result_count`.
MEMBER_AGENCY_GROUP_ID = "2085061601"
# How that tab is found, most specific first. The id above is the portal's own
# handle for the group and is what has always been used; the rest are text
# fallbacks, because a numeric group id is exactly the kind of value a portal
# renumbers without notice — and the "Run all member agency bids" sweep has
# nothing else to fall back on. A niche run that loses this tab still has its
# keyword results; the sweep IS this tab, so it fails loudly rather than
# scraping whatever group happened to be selected.
MEMBER_AGENCY_GROUP_LABEL = "Member Agency Bids"
RESULTS_ROW = "table tbody tr.mets-table-row"

# The issuing agency, as the detail page labels it. Tried in order and the first
# non-empty answer wins — BidNet's member agencies do not all use the same
# label, and the match is on the label *containing* the text, so the specific
# ones have to be asked first or "Agency" would swallow them.
#
# Only the member-agency sweep reads these (see `_extract_agency`): a niche run
# already knows what heading its bids arrived under, and re-deriving something
# it is not going to export would be pure cost.
AGENCY_FIELD_LABELS = (
    "Issuing Agency",
    "Agency Name",
    "Purchasing Organization",
    "Organization",
    "Agency",
)

# One results row. Counted independently of the links read out of it: the badge
# above is the portal's *pre-filter* number, so "rows the portal rendered" and
# "rows we could parse" are different questions, and a run that answers only the
# first cannot tell a genuinely empty search from a title cell it can no longer
# read. `_harvest_page` reports both.
#
# Every handle below is defined once in `selectors.py`, measured against a live
# results page. See there for what the portal actually serves.
ROW_SELECTOR = selectors.RESULTS_ROW
ROW_LINK_SELECTORS = selectors.ROW_LINK_SELECTORS


@dataclass
class LinkHarvest:
    """One keyword's search results, counted at every stage.

    The counts are the point: `links` alone cannot distinguish a keyword that
    matched nothing from one whose rows were all dropped in parsing, and that
    ambiguity is what let a run report hits for several keywords and export
    nothing at all.
    """

    links: list[str] = field(default_factory=list)
    rows_detected: int = 0   # <tr> the portal rendered, across every page
    rows_parsed: int = 0     # rows a solicitation link was read from
    rows_failed: int = 0     # rows no selector could read a link out of
    duplicates: int = 0      # rows whose link this keyword had already collected

    @property
    def rows_dropped(self) -> int:
        return self.rows_failed + self.duplicates


def _shown(value: int | None) -> str:
    """A count for a log line, distinguishing zero from unreadable."""
    return "unknown" if value is None else str(value)


def _as_int(value: Any) -> int | None:
    """A DOM-read number as an int, or None when it is not one.

    Everything the pagination bar and the page-size dropdown hand back arrives
    as a string attribute or as JS's single number type, and a walk that reads
    "not a number" as 0 or 1 stops early with a plausible-looking count. So the
    conversion is explicit and its failure is None.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = re.sub(r"[^0-9]", "", str(value))
    return int(digits) if digits else None

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


class BidnetScraper(BaseScraper):
    def __init__(
        self,
        run_id: str,
        search_terms: list[SearchTerm] | list[str],
        filters: SidebarFilterRequest | None = None,
        niche_label: str | None = None,
    ):
        super().__init__(run_id)
        # Every term of the run's single niche — its keywords, then its NIGP
        # codes — resolved from the database by `execute_run`. Searched one at a
        # time, never combined. Bare strings are accepted and taken as keywords,
        # so a caller with nothing but terms does not have to know about kinds.
        self.search_terms: list[SearchTerm] = [
            term if isinstance(term, SearchTerm) else SearchTerm(str(term))
            for term in search_terms
        ]
        self.niche_label = niche_label or "BidNet"
        # The sidebar filter state chosen in the frontend. Defaults to the
        # portal's own defaults (Open Solicitations, every purchasing group, no
        # other constraint), so an omitted request scrapes exactly what the
        # pre-filter scraper did.
        self.filters = filters or SidebarFilterRequest()
        self._sidebar_report: dict | None = None
        # True while the page on screen is one this run filtered. The sidebar is
        # driven once, before the first keyword, and every later search inherits
        # it — so this is the flag that says whether that inheritance is still
        # believable. Anything that navigates (a re-login, a keyword that failed
        # mid-page) clears it, and the next keyword re-establishes the filters
        # before it searches. See `open_filtered_session`.
        self._filters_live = False
        # Keywords whose search found the filters had drifted, so they had to be
        # re-applied mid-run. Empty is the expected outcome; a long list means
        # the session is not holding them and the per-keyword re-apply is what is
        # actually filtering the run.
        self._filters_reapplied_for: list[str] = []
        # The date bypass warning is reported once per run, and the request is
        # now built per keyword rather than once — so the reporting cannot hang
        # off "is this the first sidebar report?" any more.
        self._bypass_reported = False
        # Sidebar problems already reported. The filters are re-applied for every
        # keyword, so a persistent one (an option the portal stopped offering)
        # would otherwise be logged once per search.
        self._sidebar_notes: set[str] = set()
        # Fallback row-link selectors already reported (see ROW_LINK_SELECTORS).
        # Reported once per run: the portal's markup either changed or it didn't.
        self._link_fallbacks_used: set[str] = set()
        # Run-wide parsing funnel, summed across every keyword. Reconciled against
        # the exported record count at the end of the run.
        self._rows_detected = 0
        self._rows_parsed = 0
        self._rows_failed = 0
        # True once a date panel has been applied *and read back off the page*.
        # An emptied result set means something different either side of this.
        self._dates_verified = False
        # Which keywords the requested date filter actually reached. Per keyword,
        # because the sidebar is re-driven for each one and a miss on any of them
        # lets out-of-window bids into the export.
        self._dates_applied_for: list[str] = []
        self._dates_missed_for: list[str] = []
        # This run's niche folder inside the day's session root. Since
        # attachments are no longer downloaded (DOWNLOAD_DOCUMENTS) it holds
        # exactly one file — the niche's spreadsheet — and no `documents/`
        # subtree is created under it.
        run = run_manager.get_run(run_id) or {}
        folder = run.get("folder") or str(
            storage.niche_folder(storage.session_root(), self.niche_label)
        )
        self.niche_folder = Path(folder)
        self.niche_folder.mkdir(parents=True, exist_ok=True)
        self.niche_key = run.get("niche") or ""
        self.niche_slug = run.get("niche_slug")
        # Terms the portal reported zero bids for — skipped without waiting
        # on rows that were never coming, and surfaced on the run so a niche
        # whose terms all miss is obvious rather than silent.
        self._empty_keywords: list[str] = []
        # THE DEDUPLICATION ENGINE. One solicitation reached by twenty terms is
        # one row in the export, and these are what enforce it end to end:
        #
        #   _seen_bid_ids     every bid id already in the master list
        #   _records_by_id    that id -> the record that was kept, so a later
        #                     sighting can add its term to `Matched Keyword`
        #                     instead of being thrown away whole
        #   _duplicates_skipped / _duplicate_terms   what to report at the end
        #
        # There are two rounds, because a solicitation has two identities and
        # they can disagree. The link round (phase 1) keys off the URL and is
        # what stops a bid being *opened and downloaded* twice — the expensive
        # part. The record round (phase 2) keys off the reference number read
        # from the detail page, and catches what the first cannot: the portal
        # serves the same solicitation under two href shapes
        # (`/view-notice/<id>` and `/open-solicitation/<id>?target=view`), and
        # a code search reaching a bid a keyword already found by the other
        # shape would otherwise be a second row for one bid.
        self._seen_bid_ids: set[str] = set()
        self._records_by_id: dict[str, dict] = {}
        self._duplicates_skipped = 0
        self._duplicate_terms: list[str] = []
        # Repeat sightings caught in phase 1 — a bid a later term found that an
        # earlier one had already queued. Counted rather than derived, so the
        # number means one thing and is not an arithmetic guess between two
        # stages that also drop rows for other reasons.
        self._link_duplicates = 0
        # Close-date filter tallies (see app/core/closing_filter): solicitations
        # dropped for closing too soon, and those kept despite an unreadable
        # Closing Date.
        self._skipped_closing_soon = 0
        self._kept_unreadable_close = 0
        # Attachments counted across the run (not downloaded). Reported so a
        # sweep whose Documents column is all zeros is visible as such rather
        # than being taken at face value.
        self._documents_counted = 0
        # Solicitations the portal gated behind a required acknowledgement.
        # Collected so the run can list exactly which bids need a human Accept
        # rather than burying them among genuine extraction failures.
        self._acknowledgement_required: list[dict[str, str]] = []
        # Acknowledgements this run accepted on the account's behalf. Recorded
        # and reported because each one is a submission the issuing agency can
        # see — a run should never accept things without saying which.
        self._accepted_acknowledgements: list[dict[str, str]] = []
        # Whether to count each bid's attachments (never to fetch them — see
        # DOWNLOAD_DOCUMENTS). Off here and on for the member agency sweep,
        # which is the mode whose export carries a Documents column: counting
        # costs a tab render per bid, and a niche run that would not print the
        # number should not pay for it.
        self.count_documents = False

    # -- live debugging -----------------------------------------------------

    def live_debug(self, message: str, *args) -> None:
        """Announce the step that is about to happen, then pause long enough to
        watch it. Both halves are off unless HEADLESS_MODE is False, so these
        calls cost nothing in production and need no unpicking to get there."""
        if HEADLESS_MODE:
            return
        logger.info("[LIVE DEBUG]: " + message, *args)
        if DEBUG_PAUSE_SECONDS:
            time.sleep(DEBUG_PAUSE_SECONDS)

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

    # -- bid identity -------------------------------------------------------

    @staticmethod
    def _bid_key(link: str) -> str:
        """The identity of the solicitation a results-row link points at.

        The **solicitation id**, not the URL. BidNet serves the same bid under
        two href shapes, and a run that searches a niche's keywords and then its
        NIGP codes hits that constantly:

            /private/supplier/interception/view-notice/444124954092
            /private/supplier/interception/open-solicitation/444124954092?target=view

        Comparing URLs makes those two different bids — the same solicitation
        opened twice, downloaded twice and exported twice. So the trailing path
        segment (the id) is what identifies it, with the query string dropped:
        `?target=view` is a display hint, not a different bid.

        Falls back to the whole URL, lowercased and stripped of its query, when
        there is no id to find — an unknown shape must not collapse two genuinely
        different bids into one, and losing a bid is worse than a duplicate row.
        """
        base = (link or "").split("?", 1)[0].split("#", 1)[0].rstrip("/")
        segment = base.rsplit("/", 1)[-1] if "/" in base else base
        if segment.isdigit():
            return segment
        return base.lower()

    def _claim_bid(self, bid_id: str, record: dict[str, Any], matched: list[str]) -> bool:
        """Register a scraped bid, or report it as one already held.

        The single gate every record passes through on its way to the master
        list, the spreadsheet and the database. True means this bid is new and
        the caller should keep it; False means it is already in there and the
        caller must drop it.

        The id it registers is the **reference number** when the detail page
        gave one, because that is the solicitation's own identity — two links
        the portal serves under different ids are still one bid if they carry
        one reference number, and searching a niche's NIGP codes after its
        keywords is what makes that collision common. It falls back to the link
        id for a bid whose reference could not be read: an unreadable reference
        is not evidence of sameness, and deduplicating on it would merge every
        failed extraction into a single row.

        A duplicate is not simply discarded — the term that found it a second
        time is added to the kept record's `Matched Keyword`, so the export
        still says a bid was reached by both a keyword and a code. Dropping the
        row without that would lose the only evidence the code search earned its
        place.
        """
        reference_key = self._reference_key(record.get("reference_number") or "")
        key = reference_key or bid_id

        kept = self._records_by_id.get(key)
        if kept is None:
            self._seen_bid_ids.add(key)
            self._records_by_id[key] = record
            return True

        self._duplicates_skipped += 1
        self._duplicate_terms.extend(matched)
        merged = [
            term for term in (kept.get("matched_keyword") or "").split(", ") if term
        ]
        for term in matched:
            if term not in merged:
                merged.append(term)
        kept["matched_keyword"] = ", ".join(merged)
        logger.info(
            "[run %s] [DUPLICATE SKIPPED]: Bid '%s' already extracted (%s) — this "
            "sighting came from %s; %r kept as one row, now credited to %s",
            self.run_id, key, kept.get("detail_url") or "", ", ".join(matched),
            kept.get("title") or key, kept["matched_keyword"],
        )
        return False

    @staticmethod
    def _reference_key(reference_number: str) -> str:
        """A reference number reduced to what makes two of them the same bid.

        Agencies write the same number differently in different places
        ("RFP 2026-014", "rfp2026-014"), and the detail page is not consistent
        about spacing, so the comparison ignores case and anything that is not a
        letter or a digit. Empty for a reference that carries no alphanumerics at
        all, which the caller reads as "no usable id" rather than as a match —
        otherwise every unreadable bid would deduplicate into one.
        """
        return re.sub(r"[^a-z0-9]", "", (reference_number or "").lower())

    # -- flow steps ---------------------------------------------------------

    def login(self) -> None:
        self.set_step("logging_in")
        # Signing in lands on the dashboard, which has no sidebar and no search
        # results — whatever this run had filtered is gone. Said here rather than
        # at the call sites because the mid-run re-login (`ensure_logged_in`) is
        # the one that matters and is the easiest to forget.
        self._filters_live = False
        self.live_debug("Opening BidNet Direct login page...")
        # `navigate`, not a bare `driver.get`: this is also the session-recovery
        # path mid-run, and a transient DNS/socket failure there would otherwise
        # fail the keyword outright while every other navigation in the run
        # retries through it.
        self.navigate(settings.bidnet_direct_link or BASE_URL)
        self._guard_not_blocked()

        # Each wait re-checks for a bot-block first, then screenshots and raises a
        # clear message on timeout — otherwise BidNet's interstitial (which appears
        # a beat after load) just makes the element wait die with an empty-message
        # Selenium stacktrace that says nothing about why.
        self._await_login_element((By.ID, selectors.LOGIN_BUTTON), "the Login button", clickable=True).click()

        self._await_login_element((By.ID, selectors.USERNAME), "the username field")
        self.driver.find_element(By.ID, selectors.USERNAME).send_keys(settings.bidnet_username)
        self.driver.find_element(By.ID, selectors.PASSWORD).send_keys(settings.bidnet_password)
        self.driver.find_element(By.ID, selectors.LOGIN_SUBMIT).click()

        self._await_login_element(
            (By.ID, selectors.SIGNED_IN_MARKER),
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
            if self.driver.find_elements(By.ID, selectors.SIGNED_IN_MARKER):
                return  # still signed in
        except WebDriverException:
            pass  # fall through and try to recover

        logger.info("[run %s] session lost — signing in again", self.run_id)
        run_manager.add_error(self.run_id, "BidNet session expired mid-run; signed in again")
        self.login()

    def reset_search_state(self) -> None:
        """Reload the search page to get back to a known-clean slate.

        A reload clears *everything* the portal was holding — the result page the
        last harvest walked to, the result group, and the sidebar's applied
        filters. That last one is why this is no longer run between keywords: the
        filters are applied once for the session and a reload is precisely what
        destroys them.

        So it is now the entry point to `open_filtered_session` and the recovery
        path when the page can no longer be trusted (a failed keyword, results
        stuck past page 1). Every use of it is followed by re-applying the
        session's filters, and nothing else should call it.
        """
        self.set_step("resetting search")
        # Fewer retries than the default here: `ensure_logged_in` runs straight
        # after and navigates (with the full budget) if this left us anywhere
        # but the search page, so a blip is still covered — while a portal that
        # is genuinely unreachable costs one short backoff per keyword instead
        # of fifteen seconds times the whole niche.
        self.navigate(settings.bidnet_direct_link or BASE_URL, attempts=2)
        try:
            self.wait(ELEMENT_TIMEOUT).until(
                EC.presence_of_element_located((By.ID, selectors.SEARCH_INPUT))
            )
        except TimeoutException:
            # Not fatal on its own: `ensure_logged_in` runs next and recovers a
            # dropped session, and `search` waits for the box again anyway.
            logger.info("[run %s] search box not present after reset", self.run_id)
        self._await_ajax_idle()

    def search(self, keyword: str) -> None:
        """Run one keyword search and wait until *this* keyword's results are on
        the page.

        The waiting is the whole point of this method. Both of the obvious
        conditions return instantly on the second and later keywords:

        * `.searchContentGroupContainer` is already visible — it belongs to the
          previous keyword's results and is never removed, so a visibility wait
          passes before the new search has even been sent.
        * `jQuery.active == 0` is still true in the moment between the click and
          the request leaving, so an idle-wait can pass on the *pre-search* page.

        The result was a scraper that spent real time on a niche's first keyword
        and then raced through the rest reading the previous keyword's DOM: a
        stale count of 0 skipped the keyword as empty, and a stale non-zero count
        re-collected links already seen, which deduplication then swallowed. Both
        look identical from the outside — "no bids" for keywords that return
        results when searched by hand.

        So the wait is anchored on the old results being *replaced*: hold a node
        from the current group container, submit, and wait for it to go stale
        before believing anything on the page.

        An empty `keyword` is the run's bootstrap search: it opens the portal's
        whole open list purely so the sidebar renders and can be filtered (see
        `open_filtered_session`). Nothing is collected from that page.

        Since the sidebar's filters are applied once for the whole session, this
        is also where a keyword *replaces* the previous one rather than the page
        being reloaded to be rid of it — hence the two-way clear below, which
        matters more now than it did: the portal's own model holding the previous
        term would search `alpha beta` instead of `beta`, and nothing downstream
        could tell.
        """
        self.set_step(f"searching: {keyword}" if keyword else "opening the search page")
        anchor = self._results_anchor()

        box = self.wait(ELEMENT_TIMEOUT).until(
            EC.presence_of_element_located((By.ID, selectors.SEARCH_INPUT))
        )
        # Clear through the DOM as well as through Selenium: `clear()` alone can
        # leave the portal's own model holding the old term (it binds on input
        # events), which appends rather than replaces on the next search.
        box.clear()
        self.driver.execute_script(
            "arguments[0].value = '';"
            "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));",
            box,
        )
        if keyword:
            self.live_debug('Applying keyword "%s"...', keyword)
            box.send_keys(keyword)
        self.live_debug("Triggering Search button...")
        self.driver.find_element(By.ID, selectors.SEARCH_BUTTON).click()

        self._await_results_replaced(anchor)
        self.wait(ELEMENT_TIMEOUT).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, selectors.RESULT_GROUP))
        )
        # Wait for the search's own AJAX rather than a fixed sleep: the group
        # tabs are re-rendered by it, and until it lands they still show the
        # previous keyword's counts.
        self._await_ajax_idle()

    def _results_anchor(self):
        """A node from the results currently on screen, to watch for replacement.

        None on the first search of a run, when there is nothing there yet — the
        caller then falls back to waiting for the results to appear at all.
        """
        for selector in (
            f"div[search-content-group-id='{MEMBER_AGENCY_GROUP_ID}'] .solicitationCount",
            ".searchContentGroupContainer",
        ):
            try:
                return self.driver.find_element(By.CSS_SELECTOR, selector)
            except WebDriverException:
                continue
        return None

    def _await_results_replaced(self, anchor) -> None:
        """Wait for `anchor` to be torn out of the DOM by the new search.

        A search that returns the same page object without re-rendering (BidNet
        occasionally answers an identical query that way) times out here rather
        than failing: the AJAX-idle wait that follows still gives the page time,
        and the count read afterwards is then legitimately the current one.
        """
        if anchor is None:
            self.wait(ELEMENT_TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selectors.RESULT_GROUP))
            )
            return
        try:
            self.wait(ELEMENT_TIMEOUT).until(EC.staleness_of(anchor))
        except TimeoutException:
            # Deliberately not an error: failing the keyword here would skip it,
            # and a keyword skipped is exactly the outcome this whole change
            # exists to prevent. Give the page a real settle instead, so the
            # count read next has had time to become this keyword's even though
            # the container was reused.
            logger.warning(
                "[run %s] results were not re-rendered within %ss — settling before "
                "reading the count", self.run_id, ELEMENT_TIMEOUT,
            )
            time.sleep(SEARCH_SETTLE_SECONDS)

    # -- results per page ---------------------------------------------------

    # Reads the grid's page-size dropdown, and switches it, in one pass.
    #
    # Driven through the DOM rather than through Selenium's `Select` because the
    # portal binds its own `change` handler and re-runs the search from it: the
    # value has to be set *and* the event dispatched, which is exactly what a
    # native `select_by_value` cannot be relied on to do across drivers.
    #
    #   arguments[0]: the select's selector, arguments[1]: wanted page size
    # Returns {found, current, changed, url} — `url` is the option's own
    # `data-page-url`, the fallback route when the change event does not take.
    _JS_SET_PAGE_SIZE = """
    const select = document.querySelector(arguments[0]);
    if (!select) return {found: false};
    const wanted = String(arguments[1]);
    const current = select.options[select.selectedIndex];
    const option = Array.from(select.options).find(
      o => o.getAttribute('data-page-size') === wanted
    );
    const state = {
      found: true,
      current: current ? current.getAttribute('data-page-size') : null,
      offered: Array.from(select.options).map(o => o.getAttribute('data-page-size')),
      url: option ? option.getAttribute('data-page-url') : null,
      changed: false,
    };
    if (!option || state.current === wanted) return state;
    select.value = option.value;
    select.dispatchEvent(new Event('change', {bubbles: true}));
    state.changed = true;
    return state;
    """

    def set_page_size(self, size: int = PAGE_SIZE) -> int | None:
        """Ask the results grid for `size` rows a page, and wait for the reload.

        Returns the page size actually in force, or None when the dropdown is
        not on the page at all (a result set small enough that the portal does
        not render one — nothing to do, and nothing wrong).

        **Best-effort throughout.** A grid that will not change page size still
        pages perfectly well at 25; failing the run over it would trade a slow
        harvest for no harvest. Every branch below therefore logs and carries
        on, and the caller reads the returned size rather than assuming.

        The reload is waited for rather than assumed: changing the size re-runs
        the search from page 1, and harvesting during that postback reads the
        old page's rows and then walks pagination that has been replaced
        underneath it.
        """
        self.set_step(f"setting page size to {size}")
        try:
            state = self.driver.execute_script(
                self._JS_SET_PAGE_SIZE, selectors.RESULTS_PER_PAGE_SELECT, size
            )
        # Every exception, not only WebDriverException: this is an optimisation
        # sitting in the middle of the search loop, and "best-effort" has to
        # mean it whatever the page (or the driver) does. A grid that will not
        # resize still pages perfectly well at 25.
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[run %s] could not read the results-per-page control (%s) — "
                "harvesting at whatever size the grid is on",
                self.run_id, exc.__class__.__name__,
            )
            return None

        if not isinstance(state, dict) or not state.get("found"):
            logger.info(
                "[run %s] no results-per-page control on this page — one page of "
                "results, nothing to resize", self.run_id,
            )
            return None

        current = _as_int(state.get("current"))
        if not state.get("changed"):
            if current == size:
                logger.info("[run %s] results per page already %s", self.run_id, size)
            else:
                logger.warning(
                    "[run %s] the grid does not offer %s rows a page (offers %s) — "
                    "harvesting at %s",
                    self.run_id, size, state.get("offered"), _shown(current),
                )
            return current

        # The change event has been dispatched; the portal re-runs the search
        # from it. Wait for that round trip before believing anything on screen.
        self._await_ajax_idle()
        applied = self._current_page_size()
        if applied == size:
            logger.info(
                "[run %s] [PAGE SIZE]: %s → %s rows a page; the walk now covers the "
                "same results in a quarter of the page loads",
                self.run_id, _shown(current), size,
            )
            return applied

        # The select moved but the grid did not follow it. Each option carries a
        # real GET that re-runs the current search at that size — the portal's
        # own route, taken directly.
        url = state.get("url")
        if url:
            logger.info(
                "[run %s] the page-size change did not re-run the search — "
                "following the option's own link instead", self.run_id,
            )
            try:
                self.driver.get(self._abs_url(str(url)))
                self._await_ajax_idle()
                applied = self._current_page_size()
            except Exception as exc:  # noqa: BLE001 — the fallback is optional too
                logger.warning(
                    "[run %s] page-size link failed (%s)", self.run_id, exc.__class__.__name__
                )

        if applied != size:
            logger.warning(
                "[run %s] could not set the page size to %s (it reads %s) — the "
                "harvest will take more pages but collect the same bids",
                self.run_id, size, _shown(applied),
            )
        return applied

    def _current_page_size(self) -> int | None:
        """The page size the grid is currently rendering, off its own dropdown."""
        script = """
        const select = document.querySelector(arguments[0]);
        if (!select) return null;
        const option = select.options[select.selectedIndex];
        return option ? option.getAttribute('data-page-size') : null;
        """
        try:
            value = self.driver.execute_script(script, selectors.RESULTS_PER_PAGE_SELECT)
        except Exception:  # noqa: BLE001 — unreadable is "unknown", never a failure
            return None
        return _as_int(value)

    # -- pagination ---------------------------------------------------------

    # The pagination bar's own account of where the walk is: which page is on
    # screen, and how many there are.
    #
    #   arguments[0]: the bar, [1]: the current-page mark, [2]: the Last link,
    #   [3]: every numbered page link
    #
    # `total` comes from the Last link's `data-page-number` (19 on a 19-page
    # result set), falling back to the highest page the bar lists when the whole
    # range fits and no Last link is rendered. Null for either value means "the
    # bar does not say", which callers must treat as unknown rather than as a
    # number — a walk that reads a missing total as 1 stops after one page.
    _JS_PAGE_STATE = """
    const bar = document.querySelector(arguments[0]);
    if (!bar) return null;
    const num = (el, attr) => {
      if (!el) return null;
      const raw = attr ? el.getAttribute(attr) : el.textContent;
      const n = parseInt((raw || '').replace(/[^0-9]/g, ''), 10);
      return Number.isFinite(n) ? n : null;
    };
    const current = num(bar.querySelector(arguments[1]));
    let total = num(bar.querySelector(arguments[2]), 'data-page-number');
    if (total === null) {
      for (const a of bar.querySelectorAll(arguments[3])) {
        const n = num(a, 'data-page-number');
        if (n !== null && (total === null || n > total)) total = n;
      }
      if (total !== null && current !== null && current > total) total = current;
    }
    return {current: current, total: total};
    """

    def _page_state(self) -> tuple[int | None, int | None]:
        """(current page, total pages) as the pagination bar reports them.

        Either can be None — a single page of results renders no bar at all, and
        a bar that changes shape must degrade to "unknown" rather than to a
        confident wrong number.
        """
        try:
            state = self.driver.execute_script(
                self._JS_PAGE_STATE,
                selectors.PAGINATION_CONTAINER,
                selectors.PAGINATION_CURRENT,
                selectors.PAGINATION_LAST,
                selectors.PAGINATION_NUMBERED,
            )
        except Exception:  # noqa: BLE001 — an unreadable bar is "unknown", not a fault
            return None, None
        if not isinstance(state, dict):
            return None, None
        return _as_int(state.get("current")), _as_int(state.get("total"))

    def _ensure_first_result_page(self) -> bool:
        """Put the results back on page 1 before they are harvested.

        This is the half of the old per-keyword reload that still has to happen.
        `collect_links` walks to the *last* page of every keyword's results, and
        the next keyword is now typed into the box from there instead of into a
        freshly loaded page. If BidNet answers a new search from page 7, the
        harvest starts at page 7 and pages 1-6 are lost in silence — the run
        would report a smaller, entirely plausible number of bids.

        So it is checked rather than assumed, on the cheapest available signal:
        the pagination bar's own current-page mark, falling back to "is there a
        working way back?". Returns True when the results are on page 1 (or there
        is only one page, or the bar cannot be read — nothing to act on), and
        False when it could not get back, which the caller repairs by reloading
        and re-filtering.
        """
        page = self._read_current_page()
        if page is None or page == 1:
            return True

        logger.info(
            "[run %s] results came back on page %s — returning to page 1 before "
            "harvesting", self.run_id, page or "?",
        )
        for selector in selectors.FIRST_PAGE_SELECTORS:
            try:
                links = self.driver.find_elements(By.CSS_SELECTOR, selector)
            except WebDriverException:
                continue
            if not links:
                continue
            try:
                self.driver.execute_script("arguments[0].click();", links[0])
            except WebDriverException:
                continue
            self._await_ajax_idle()
            if self._read_current_page() in (None, 1):
                return True

        logger.warning(
            "[run %s] could not return the results to page 1; reloading the search "
            "and re-applying the session's filters instead", self.run_id,
        )
        return False

    def _read_current_page(self) -> int | None:
        """Which results page is on screen, or None if the bar does not say.

        A result set with a single page renders no pagination at all, which is
        None here and is page 1 to every caller — hence the `in (None, 1)`
        checks around it rather than an equality test.
        """
        return self._page_state()[0]

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

    def _member_agency_tab(self):
        """The "Member Agency Bids" result-group tab, or None if it is not there.

        By id first, then by the tab's own visible text — a group id is a number
        the portal owns and can renumber, and a run that silently scraped
        whichever group was selected instead would report a plausible, wrong
        set of bids.
        """
        try:
            found = self.driver.find_elements(
                By.CSS_SELECTOR, f"div[search-content-group-id='{MEMBER_AGENCY_GROUP_ID}']"
            )
            if found:
                return found[0]
        except WebDriverException:
            pass

        try:
            groups = self.driver.find_elements(By.CSS_SELECTOR, selectors.RESULT_GROUP)
        except WebDriverException:
            return None
        for group in groups:
            try:
                if MEMBER_AGENCY_GROUP_LABEL.lower() in (group.text or "").lower():
                    logger.warning(
                        "[run %s] the Member Agency group is no longer at id %s — found "
                        "it by its label instead. selectors/MEMBER_AGENCY_GROUP_ID "
                        "needs updating.", self.run_id, MEMBER_AGENCY_GROUP_ID,
                    )
                    return group
            except WebDriverException:
                continue
        return None

    def filter_member_agency(self) -> None:
        self.set_step("filtering_member_agency")
        tab = self._member_agency_tab()
        if tab is None:
            raise WebDriverException(
                f"the {MEMBER_AGENCY_GROUP_LABEL!r} result group is not on the page — "
                "neither by its group id nor by its label"
            )
        tab.click()
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

    # -- the session's filters ----------------------------------------------

    def open_filtered_session(self) -> None:
        """Put the sidebar into the requested state **once**, before any keyword.

        The date panels are the reason this exists. They are part of the search
        form, not of a keyword's results: set once, they filter every search the
        session makes afterwards. Driving them per keyword meant four postbacks
        (tick the mode, write the dates, press Apply, wait out the reload) times
        twenty-odd keywords on a portal where each is a full page round-trip, and
        every one of those was another chance for the panel to come back empty —
        which is precisely the failure this scraper's date code is littered with
        scar tissue from.

        The sidebar only renders on a results page, so one is opened first: a
        search with an empty keyword box, which returns the portal's whole open
        list. Nothing is collected from it. It exists to be filtered, and the
        first keyword replaces it.

        Failing here is not fatal, in either of its two shapes. If no results
        page can be opened at all, `_filters_live` stays False and the first
        keyword re-establishes the session before it searches. If the page opens
        but the sidebar will not take, every keyword's own check finds the
        filters missing and applies them to its own results — which is exactly
        the flow this replaced, so the worst case is the old cost, not an
        unfiltered run.
        """
        self.set_step("applying_filters")
        self.reset_search_state()
        self.ensure_logged_in()
        self.live_debug("Opening the results page the session's filters go on...")
        try:
            self.search("")
        except (TimeoutException, WebDriverException) as exc:
            logger.warning(
                "[run %s] could not open a results page to filter (%s) — the "
                "sidebar will be applied on the first keyword's own results instead",
                self.run_id, exc.__class__.__name__,
            )
            return

        # Before anything is clicked. The banner renders over the sidebar's
        # Apply buttons, the result-group tabs and the pagination bar alike, so
        # dismissing it once here is cheaper than discovering it three times as
        # an intercepted click.
        self._dismiss_cookie_banner()

        try:
            report = self.apply_sidebar_filters()
        except (TimeoutException, WebDriverException) as exc:
            # The panels swallow their own per-panel failures, so getting here
            # means something broader went wrong with the page. The run is not
            # abandoned for it: the results page is still open and every keyword
            # re-checks the filters, so this degrades to the old per-keyword
            # behaviour rather than to an unfiltered run.
            logger.warning(
                "[run %s] the sidebar could not be applied to the session's results "
                "page (%s) — each keyword will apply it to its own results instead",
                self.run_id, exc.__class__.__name__,
            )
            report = {}
        # True even when the apply was imperfect: the page in front of us is the
        # one this run filtered, and the per-keyword check is what decides
        # whether it still holds. Left False here, every keyword would re-open a
        # bootstrap results page before searching — slower than the flow this
        # replaced, for no extra safety.
        self._filters_live = True
        logger.info(
            "[run %s] [SESSION FILTER]: applied once for the whole run — %s. "
            "Every keyword below is searched with this already in force.",
            self.run_id, self.filters.summary(),
        )
        if [name for name, _ in self._effective_filters().dates()] and not report.get("dates"):
            # The one-time application is where a date panel failure is cheapest
            # to see and most costly to miss: it is about to govern every keyword
            # in the run, not one of them.
            logger.warning(
                "[run %s] [SESSION FILTER]: the date panel(s) did not verify on the "
                "page they were applied to. Each keyword will re-check and re-apply "
                "them, but if that keeps failing this run's bids are not date-filtered.",
                self.run_id,
            )

    def _ensure_filters_live(self) -> None:
        """Re-establish the session's filters if the page can no longer be
        trusted to hold them. A no-op in the ordinary case, which is the point —
        the filters are applied once and then simply kept."""
        if self._filters_live:
            return
        self.open_filtered_session()

    def confirm_filters_active(self, keyword: str) -> None:
        """Confirm this keyword's search inherited the session's filters.

        Read-only in the normal case: one round-trip that re-reads the status
        radio, the list panels' hidden fields and both date panels off the page
        the search just returned. That is the whole per-keyword cost of the
        filters now, against four postbacks before.

        The re-apply is the exception path. "The filters persist across a search"
        is a claim about BidNet's form, and a session that quietly dropped its
        Published Date window would put out-of-window bids into the export under
        a run record stating the window was applied — so it is verified per
        keyword rather than assumed, and repaired on the spot when it is not.
        """
        request = self._effective_filters()
        requested_dates = [name for name, _ in request.dates()]
        intact, drifted = SidebarDriver(
            self.driver, note=self._note_sidebar, debug=self.live_debug
        ).state_intact(request)

        if intact:
            self._dates_verified = bool(requested_dates)
            if requested_dates:
                self._dates_applied_for.append(keyword)
            logger.info(
                "[run %s]  ├── [SESSION FILTER]: still active for this search (%s).",
                self.run_id, self.filters.summary(),
            )
            return

        logger.warning(
            "[run %s] keyword %r: the session's sidebar filters did not survive this "
            "search (%s) — re-applying them to this keyword's results",
            self.run_id, keyword, "; ".join(drifted),
        )
        self._filters_reapplied_for.append(keyword)
        run_manager.update_run(
            self.run_id, filters_reapplied_keywords=list(self._filters_reapplied_for)
        )
        self.apply_sidebar_filters(keyword)

    def _effective_filters(self) -> SidebarFilterRequest:
        """The request the sidebar is actually driven with — `self.filters`, less
        anything the testing-phase switches strip out.

        Testing-phase bypass: drop the date panels before the request reaches the
        sidebar, so no date control is touched at all. Stripping them here rather
        than inside SidebarDriver keeps the bypass in one place and leaves the
        panel code exactly as production runs it.

        It also has to be the *same* request the per-keyword check verifies
        against: a check that still expected the panels the setup was told to
        skip would report drift on every keyword and re-apply what the switch
        exists to avoid touching.
        """
        request = self.filters
        if APPLY_DATE_FILTERS or not request.dates():
            return request

        skipped = ", ".join(f"{n} {v.describe()}" for n, v in request.dates())
        self.live_debug("Date filters BYPASSED (%s) — searching on keyword + status.", skipped)
        if not self._bypass_reported:
            self._bypass_reported = True
            logger.warning(
                "[run %s] APPLY_DATE_FILTERS is False — the requested date "
                "filter(s) (%s) were NOT applied. Results are unfiltered by "
                "date; this run's counts are wider than what was asked for.",
                self.run_id, skipped,
            )
            run_manager.add_warning(
                self.run_id,
                f"Date filters were bypassed for testing (APPLY_DATE_FILTERS=False): "
                f"{skipped} not applied. Results are not filtered by date.",
            )
        return request.model_copy(update={"published_date": None, "closing_date": None})

    def apply_sidebar_filters(self, keyword: str = "") -> dict:
        """Drive every sidebar panel into the requested state on the current page.

        Called twice in a healthy run: once by `open_filtered_session` before any
        keyword (`keyword=""`), and never again — the filters stay applied. The
        other caller is `confirm_filters_active`, repairing a session that lost
        them, which is where the per-keyword tallies below come from.

        A panel the portal did not render is reported into the run's errors and
        skipped: a partially-filtered run returns a superset of what was asked
        for, which is still usable, whereas aborting returns nothing.
        """
        self.set_step("applying_filters")
        # Measured either side of the postback. The sidebar is the one stage that
        # can legitimately take a page of results down to nothing, so without
        # these two numbers an over-narrow filter is indistinguishable from a
        # search that never matched — which is exactly how a run reported hits
        # for several keywords and exported nothing.
        rows_before = self._visible_row_count()
        count_before = self.result_count()

        request = self._effective_filters()
        # What this search is *supposed* to be filtered by, captured before the
        # sidebar runs so the outcome can be checked against it.
        requested_dates = [name for name, _ in request.dates()]

        self.live_debug("Interacting with sidebar filters...")
        report = SidebarDriver(
            self.driver, note=self._note_sidebar, debug=self.live_debug
        ).apply(request)

        rows_after = self._visible_row_count()
        count_after = self.result_count()
        logger.info(
            "[run %s] [SIDEBAR FILTER]: %s → %s bid(s) reported by the portal; "
            "%s → %s row(s) on the current page",
            self.run_id, _shown(count_before), _shown(count_after),
            _shown(rows_before), _shown(rows_after),
        )
        # Whether a date panel is in force *and verified on the page*. This is
        # the difference between a filter doing its job and one that silently
        # broke, and it decides how loudly the emptiness below is reported.
        self._dates_verified = bool(report.get("dates"))

        # Tallied per keyword, not once per run. "The date filter applied" is one
        # fact per keyword rather than one per run, and the run record used to
        # carry only the first of them (`_sidebar_report` is written once): a
        # panel that applied on keyword 1 and silently missed on keyword 9 looked
        # identical to one that worked throughout, while quietly letting
        # out-of-window bids into the export. In the current flow most keywords
        # are tallied by `confirm_filters_active` reading the panels back; this
        # branch covers the ones that had to be re-applied. The session's own
        # application (`keyword=""`) belongs to no keyword and is not tallied.
        if requested_dates and keyword:
            if report.get("dates"):
                self._dates_applied_for.append(keyword)
            else:
                self._dates_missed_for.append(keyword)
                logger.warning(
                    "[run %s] keyword %r: the date filter did NOT apply to this "
                    "search — its results are unfiltered by date",
                    self.run_id, keyword,
                )

        if rows_before and rows_after == 0:
            dates = ", ".join(f"{name} {value.describe()}" for name, value in self.filters.dates())
            if self._dates_verified:
                # The panel read its own values back off the page, so this is the
                # date window doing exactly what was asked of it. Reporting a
                # working filter as a run error is how a real failure gets lost
                # among a dozen expected ones — it goes in the log, not the
                # errors list.
                logger.info(
                    "[run %s] [SIDEBAR FILTER]: all %s row(s) fall outside %s — "
                    "nothing to collect from this search",
                    self.run_id, rows_before, dates,
                )
            else:
                # No verified date filter, so an emptied result set is unexplained
                # and is the single most likely cause of an empty export.
                message = (
                    f"The sidebar filters removed every result: the portal returned "
                    f"{rows_before} row(s) for this search and none survived filtering. "
                    f"Filters applied: {self.filters.summary()}. If bids were "
                    f"expected, widen or clear the sidebar filters and re-run."
                )
                self._note_sidebar(message)
                logger.warning("[run %s] %s", self.run_id, message)
        # Report once per run. In the normal flow this *is* the run's only
        # application, made before the first keyword; a later repair does not
        # overwrite it, because what the run promised is what it set out with.
        if self._sidebar_report is None:
            self._sidebar_report = report
            run_manager.update_run(
                self.run_id,
                filters=self.filters.model_dump(exclude_none=True),
                filters_summary=self.filters.summary(),
                filters_applied=report,
            )
            logger.info("[run %s] sidebar filters applied: %s", self.run_id, report)
        return report

    def _note_sidebar(self, message: str) -> None:
        """Record a sidebar problem once per run, however many keywords hit it."""
        if message in self._sidebar_notes:
            return
        self._sidebar_notes.add(message)
        run_manager.add_error(self.run_id, message)

    def collect_links(self) -> LinkHarvest:
        """Walk every results page, collecting solicitation detail links.

        The walk is driven by the pagination bar's **own page numbers**, not by
        guessing when the list ended. It reads "page 3 of 19" off the bar, and
        keeps going until the page it is on is the last one — so a click that
        did not take is a retry, and a page that renders slowly is a wait,
        where both used to end the walk and report a short harvest as a
        complete one. That is what turned 1,850 member agency bids into 574.

        Three things make it survive a grid that re-renders under it:

        * **Nothing is held across a page change.** The next-page control is
          re-found every iteration, so a stale handle is impossible by
          construction rather than by catching StaleElementReferenceException.
        * **Advancing is confirmed by number.** After clicking, it waits for the
          bar to *say* it is on the expected page — a far tighter signal than
          "the first row's href is different", which passes on a half-rendered
          page and fails on a page whose first row legitimately repeats.
        * **A page that will not advance is retried, then reported.** Stopping
          is still possible; stopping *silently* is not.

        Returns a `LinkHarvest` rather than a bare list so the caller can report
        the whole funnel — rows the portal rendered, rows a link was read from,
        rows dropped — instead of only the number that survived. A keyword that
        reports hits and yields no links is a parsing failure, and it used to be
        indistinguishable from a keyword that genuinely matched nothing.
        """
        harvest = LinkHarvest()
        page_num, total = self._page_state()
        page_num = page_num or 1
        if total and total > 1:
            logger.info(
                "[run %s] [PAGINATION]: %s page(s) to walk at %s rows a page",
                self.run_id, total, _shown(self._current_page_size()),
            )

        while True:
            self.raise_if_stopped()
            self._harvest_page(harvest, page_num)
            logger.info(
                "[run %s] page %s%s: %s row(s) detected, %s parsed, %s dropped "
                "(%s unique link(s) so far)",
                self.run_id, page_num, f" of {total}" if total else "",
                harvest.rows_detected, harvest.rows_parsed,
                harvest.rows_dropped, len(harvest.links),
            )
            run_manager.update_run(self.run_id, bids_found=len(harvest.links))

            # The bar says this is the last page. The most reliable stop there
            # is, and the only one that cannot be confused with a page that
            # merely failed to load.
            if total is not None and page_num >= total:
                logger.info(
                    "[run %s] reached the last page (%s of %s)",
                    self.run_id, page_num, total,
                )
                break

            if page_num >= MAX_PAGES:
                message = (
                    f"stopped paginating at the {MAX_PAGES}-page safety limit"
                    + (f" with {total - page_num} page(s) still unread" if total else "")
                    + ". Narrow the sidebar filters to bring the list within it."
                )
                logger.error("[run %s] %s", self.run_id, message)
                run_manager.add_error(self.run_id, message)
                break

            if not self._advance_page(page_num, total):
                break
            page_num += 1
            # Re-read rather than trusting the increment: the bar is the
            # authority on where the walk actually is, and a portal that skipped
            # or repeated a page must not be papered over by our own counter.
            seen, total = self._page_state()
            if seen is not None:
                page_num = seen

        # Every page the bar promised, against every page actually harvested.
        # The whole point of reading the total: a walk that ends early now says
        # so with both numbers, instead of returning a plausible short list.
        if total is not None and page_num < total:
            message = (
                f"pagination stopped on page {page_num} of {total} — "
                f"{total - page_num} page(s) of results were NOT collected."
            )
            logger.error("[run %s] %s", self.run_id, message)
            run_manager.add_error(self.run_id, message)

        return harvest

    def _advance_page(self, page_num: int, total: int | None) -> bool:
        """Move to the page after `page_num`. False when it could not be done.

        Retried rather than abandoned on the first miss: a next-page click that
        does not land is usually the grid still settling, and one transient miss
        used to cost every remaining page.
        """
        for attempt in range(1, PAGE_ADVANCE_ATTEMPTS + 1):
            # Re-found every attempt. A handle taken before the last click is
            # detached the moment the grid re-renders, and reusing it is the
            # StaleElementReferenceException this loop exists to never see.
            next_button = self._find_next_button()
            if next_button is None:
                if total is not None and page_num < total:
                    # The bar promised more pages and then withheld the control
                    # to reach them — a re-render caught mid-flight. Settle and
                    # look again rather than calling it the end of the list.
                    if attempt < PAGE_ADVANCE_ATTEMPTS:
                        logger.info(
                            "[run %s] page %s of %s: no next-page control yet — "
                            "waiting for the grid to finish rendering (attempt %s)",
                            self.run_id, page_num, total, attempt,
                        )
                        self._await_ajax_idle(timeout=PAGINATION_TIMEOUT)
                        continue
                    return False
                logger.info("[run %s] no further pages", self.run_id)
                return False

            if not self._click_next(next_button, page_num):
                return False
            if self._await_page(page_num + 1):
                return True

            logger.info(
                "[run %s] page %s did not advance on attempt %s — retrying",
                self.run_id, page_num, attempt,
            )

        message = (
            f"pagination stalled on page {page_num}: the grid would not advance "
            f"after {PAGE_ADVANCE_ATTEMPTS} attempts."
        )
        logger.error("[run %s] %s", self.run_id, message)
        run_manager.add_error(self.run_id, message)
        return False

    def _await_page(self, wanted: int) -> bool:
        """Wait until the pagination bar says it is on page `wanted`.

        The sentinel the walk turns on. Waiting on the bar's own number is what
        makes "the page changed" a fact rather than an inference: a half-rendered
        page has not updated it, and a page whose first row happens to repeat the
        last one's has.
        """
        def _arrived(_driver) -> bool:
            return self._page_state()[0] == wanted

        try:
            self.wait(PAGINATION_TIMEOUT).until(_arrived)
        except TimeoutException:
            return False
        self._await_ajax_idle()
        return True
    def _harvest_page(self, harvest: LinkHarvest, page_num: int) -> None:
        """Read one results page into `harvest`, row by row.

        Rows and links are counted separately on purpose: `result_count` reads
        the group tab's badge, which is the portal's own number for the search,
        so a keyword can report hits and still contribute nothing if the rows
        arrive but no link can be read out of them. Every dropped row is logged
        with its index and the selectors that missed it, so the next run says
        which cell changed instead of reporting a silent zero.
        """
        # Read every row's href in a single JS pass rather than walking Selenium
        # element handles one at a time.
        #
        # The handles are the problem: a lazy re-render partway through the loop
        # detaches the rows still to be visited, every `row.find_element` on them
        # raises StaleElementReferenceException, and — because that is a
        # WebDriverException — they were counted as *unparseable* rows. Silent
        # under-collection dressed up as a parsing failure, on a page that was
        # merely still settling. One pass cannot go stale mid-iteration, and it
        # is also a single round-trip instead of one per row per selector.
        rows = self._read_rows(page_num)
        if rows is None:
            # The DOM changed under the read. Let it settle and take one more
            # look before believing the page.
            self._await_ajax_idle(timeout=PAGINATION_TIMEOUT)
            rows = self._read_rows(page_num) or []

        harvest.rows_detected += len(rows)
        for row in rows:
            href = row.get("href")
            if not href:
                harvest.rows_failed += 1
                logger.warning(
                    "[run %s] page %s row %s: no solicitation link — none of %s "
                    "matched. Row text: %r",
                    self.run_id, page_num, row.get("index"),
                    ", ".join(f"'{s}'" for s in ROW_LINK_SELECTORS),
                    (row.get("text") or "")[:160],
                )
                continue
            self._note_fallback(row.get("selector"))
            harvest.rows_parsed += 1
            full = self._abs_url(href)
            if full in harvest.links:
                # Already seen on an earlier page of this same keyword — the
                # portal repeats a row when the list shifts under pagination.
                harvest.duplicates += 1
                continue
            harvest.links.append(full)

    # Reads every row's link in one pass, trying each selector in order per row
    # and reporting which one matched, so a fallback can be surfaced once.
    #   arguments[0]: row selector, arguments[1]: ordered link selectors
    _JS_READ_ROWS = """
    const rows = document.querySelectorAll(arguments[0]);
    const selectors = arguments[1];
    const out = [];
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i];
      let href = null, used = null;
      for (const selector of selectors) {
        const a = row.querySelector(selector);
        if (a && a.href) { href = a.href; used = selector; break; }
      }
      out.push({
        index: i + 1,
        href: href,
        selector: used,
        text: href ? '' : (row.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 160),
      });
    }
    return out;
    """

    def _read_rows(self, page_num: int) -> list[dict] | None:
        """Every row on the current page as {index, href, selector, text}, or
        None if the page could not be read (a re-render mid-pass)."""
        try:
            return self.driver.execute_script(
                self._JS_READ_ROWS, ROW_SELECTOR, list(ROW_LINK_SELECTORS)
            )
        except WebDriverException as exc:
            logger.info(
                "[run %s] page %s could not be read (%s) — settling and retrying once",
                self.run_id, page_num, exc.__class__.__name__,
            )
            return None

    def _note_fallback(self, selector: str | None) -> None:
        """Report once per run that the primary row-link selector stopped
        matching and a fallback is carrying the harvest."""
        if not selector or selector == ROW_LINK_SELECTORS[0]:
            return
        if selector in self._link_fallbacks_used:
            return
        self._link_fallbacks_used.add(selector)
        message = (
            f"BidNet results rows no longer match '{ROW_LINK_SELECTORS[0]}' — "
            f"reading solicitation links via the fallback selector '{selector}' "
            f"instead. The rows are still being collected; the portal's markup has "
            f"changed and the primary selector needs updating."
        )
        logger.warning("[run %s] %s", self.run_id, message)
        run_manager.add_warning(self.run_id, message)

    def _first_row_link(self) -> str | None:
        """The first results row's href, read the same way the harvest reads
        every row — one JS pass, same fallback order.

        No longer the pagination sentinel: the walk turns on the pagination
        bar's own page number now (`_await_page`), which a half-rendered page
        cannot satisfy and a page whose first row legitimately repeats cannot
        break. Kept as the cheapest way to ask "what is the grid showing right
        now", and used by the tests that pin the row-link fallback order.
        """
        rows = self._read_rows(page_num=0)
        if not rows:
            return None
        return rows[0].get("href") or None

    def _visible_row_count(self) -> int | None:
        """Rows rendered on the current results page, or None if unreadable.
        Page-scoped, not the whole result set — enough to tell "the filter
        emptied the table" from "the filter narrowed it"."""
        try:
            return len(self.driver.find_elements(By.CSS_SELECTOR, ROW_SELECTOR))
        except WebDriverException:
            return None

    def _click_next(self, next_button, page_num: int) -> bool:
        """Advance to the next results page. False if it could not be clicked.

        Native click first, then the cookie banner dismissed, then a scripted
        click. The banner is the reason this is three steps rather than one: it
        renders over the pagination bar on a fresh session, so Selenium's native
        click lands on the banner and raises ElementClickInterceptedException —
        which used to end the walk at page 1 and report it as the last page.

        On a keyword search that is usually invisible (most keywords return a
        single page). On a member-agency sweep it is the difference between 25
        bids and 1,850, which is what it cost before this existed.
        """
        try:
            next_button.click()
            return True
        except WebDriverException as exc:
            logger.info(
                "[run %s] page %s: the native next-page click did not land (%s) — "
                "clearing anything over it and clicking through the DOM",
                self.run_id, page_num, exc.__class__.__name__,
            )

        self._dismiss_cookie_banner()
        try:
            self.driver.execute_script("arguments[0].click();", next_button)
            return True
        except WebDriverException as exc:
            message = (
                f"pagination stopped at page {page_num}: the next-page control could "
                f"not be clicked ({exc.__class__.__name__}). Later pages were NOT "
                f"collected."
            )
            logger.error("[run %s] %s", self.run_id, message)
            run_manager.add_error(self.run_id, message)
            return False

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

    def process_bid(self, link: str) -> dict[str, Any] | None:
        """Open one solicitation and scrape its fields. **One page load, no
        downloads** — see DOWNLOAD_DOCUMENTS.

        Returns None when the solicitation is skipped by the close-date filter
        (closing sooner than MIN_DAYS_UNTIL_CLOSE). Called once per distinct
        solicitation: the search phase has already deduplicated by link, so a bid
        found by several of the niche's keywords is never opened twice."""
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
        # record flowed on as if it were real. Retry once — then flag whatever
        # we end up with.
        #
        # **The session is checked before the retry**, and that is the whole
        # value of this branch on a long run. BidNet expires a session after
        # about an hour, and it does not do so with an error: it simply serves
        # a signed-out page, which has no `.mets-field` on it, so every field
        # comes back "" and the bid is filed as EXTRACTION_FAILED. A member
        # agency sweep opens a couple of thousand detail pages back to back and
        # crosses that hour in the middle — one real run read 1,321 bids
        # correctly and then failed the remaining 538 in a row, every one of
        # them a page the portal would have served fine to a signed-in browser.
        #
        # Reloading alone could never fix that; signing in again does, and the
        # cost when the session is healthy is a single DOM lookup, paid only by
        # a bid that already failed to read.
        if not any(record.values()):
            logger.warning(
                "[run %s] no fields could be read from %s — checking the session "
                "and reloading once", self.run_id, link,
            )
            self.ensure_logged_in()
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

        # Keep only solicitations still at least MIN_DAYS_UNTIL_CLOSE days from
        # their Closing Date. An unreadable closing date is kept and tallied.
        # Returning None tells the caller to drop this solicitation (and it is
        # deliberately left out of the reuse cache, so it never short-circuits
        # the filter for a later keyword group).
        #
        # Off for the testing phase (APPLY_CLOSE_DATE_FILTER): every solicitation
        # the portal returned is kept, so the run's count is comparable with a
        # manual search's.
        if APPLY_CLOSE_DATE_FILTER:
            days_left = days_until_close(record.get("closing_date"))
            if days_left is None:
                self._kept_unreadable_close += 1
            elif days_left < MIN_DAYS_UNTIL_CLOSE:
                self._skipped_closing_soon += 1
                return None

        # The documents tab is opened to *count* the attachments, never to
        # fetch them (DOWNLOAD_DOCUMENTS) and never into a per-bid folder. Left
        # absent rather than set to 0 when the count could not be read, so the
        # export can tell "no attachments" from "we could not tell".
        if self.count_documents:
            count = self._count_documents(link)
            if count is not None:
                record["documents_count"] = str(count)
                self._documents_counted += count
        return record

    # Every labelled field on the page in one pass, matched against the labels
    # above in order. One round trip and no per-miss logging, because the
    # member-agency sweep calls this once per bid across thousands of bids —
    # `_extract_field` would be five XPath lookups and up to five "field not on
    # the page" lines each, which is a log nobody can read and a portal round
    # trip nobody needs.
    #   arguments[0]: the .mets-field selector, arguments[1]: ordered labels
    _JS_READ_AGENCY = """
    const fields = document.querySelectorAll(arguments[0]);
    const labels = arguments[1];
    for (const label of labels) {
      const wanted = label.toLowerCase();
      for (const field of fields) {
        const head = (field.querySelector('.mets-field-label') || field);
        if (!(head.innerText || '').toLowerCase().includes(wanted)) continue;
        const body = field.querySelector('.mets-field-body p, .mets-field-body');
        const value = ((body ? body.innerText : '') || '').trim();
        if (value) return value;
      }
    }
    return '';
    """

    # -- attachments, counted but never fetched -----------------------------

    # Every attachment anchor currently in the DOM, deduplicated by href.
    #
    # Deduplication is the difference between "links on the page" and "documents
    # this bid has": the portal routinely renders the same attachment twice (a
    # row link and an icon link), and reporting the raw anchor count would
    # overstate every such bid.
    #   arguments[0]: the attachment selector
    _JS_COUNT_ATTACHMENTS = """
    const seen = new Set();
    for (const a of document.querySelectorAll(arguments[0])) {
      const href = a.getAttribute('href') || a.href || '';
      if (!href || href.toLowerCase().startsWith('javascript')) continue;
      seen.add(href);
    }
    return seen.size;
    """

    # The Documents tab's own count. Informational only — never a gate on
    # whether to look, because reading it as one is precisely what produced
    # false zeros on bids that did have attachments.
    #   arguments[0]: the tab ids to try, arguments[1]: the badge selector
    _JS_DOC_BADGE = """
    for (const id of arguments[0]) {
      const tab = document.getElementById(id);
      if (!tab) continue;
      const badge = tab.querySelector(arguments[1]);
      const text = badge ? (badge.textContent || '') : (tab.textContent || '');
      const digits = text.replace(/[^0-9]/g, '');
      if (digits !== '') return parseInt(digits, 10);
    }
    return null;
    """

    def _count_documents(self, link: str = "") -> int | None:
        """How many attachments this solicitation carries. **Nothing is fetched.**

        Returns the count, or None when the page cannot be read at all — which
        callers must keep distinct from 0, since "no attachments" and "we could
        not tell" are different facts about a bid.

        Three steps, cheapest first, because a sweep pays this once per bid
        across a couple of thousand of them:

        1. **Look at the DOM as it stands.** Some solicitations render their
           attachment anchors without the tab being touched, and those cost
           nothing at all.
        2. **Read the tab's badge.** If anchors are already present and the badge
           agrees with them, the answer is settled and no tab is opened.
        3. **Open the tab and wait for the anchors.** Only when the first two do
           not agree — the tab body is rendered lazily, so until it is opened the
           anchors genuinely are not there.

        The anchor count wins over the badge wherever both are available: the
        badge is what the portal claims, the anchors are what it actually
        serves, and where they disagree it is the badge that has been wrong.
        """
        found = self._attachment_count()
        badge = self._document_badge()

        # Settled without touching the page: what is rendered matches what the
        # tab claims.
        if found and badge is not None and found == badge:
            return found

        # A badge that positively reads 0 and no anchors on the page is almost
        # certainly a bid with no attachments — confirmed with a short look at
        # the tab body rather than trusted outright.
        timeout = DOC_ZERO_TIMEOUT if (badge == 0 and not found) else DOC_TAB_TIMEOUT
        if not self._open_documents_tab():
            # No tab to open. Whatever is on the page is the whole answer.
            if found:
                return found
            return badge

        settled = self._await_attachments(timeout, expected=badge)
        if settled:
            return settled
        # The tab rendered nothing countable. Fall back to the badge, then to
        # whatever was on the page before it was opened.
        if badge is not None:
            if badge:
                logger.info(
                    "[run %s] documents tab reports %s file(s) but none could be "
                    "counted%s — recording the tab's own number",
                    self.run_id, badge, f" for {link}" if link else "",
                )
            return badge
        return found or None

    def _attachment_count(self) -> int:
        try:
            value = self.driver.execute_script(
                self._JS_COUNT_ATTACHMENTS, selectors.ATTACHMENT_SELECTOR
            )
        except Exception:  # noqa: BLE001 — an unreadable page is 0 found, not a fault
            return 0
        return int(value) if isinstance(value, (int, float)) else 0

    def _document_badge(self) -> int | None:
        try:
            value = self.driver.execute_script(
                self._JS_DOC_BADGE, list(selectors.DOCS_TAB_IDS), selectors.DOCS_TAB_BADGE
            )
        except Exception:  # noqa: BLE001
            return None
        return int(value) if isinstance(value, (int, float)) else None

    def _open_documents_tab(self) -> bool:
        """Click the Documents tab so the portal renders its body.

        A scripted click, not a native one: the tab anchors sit under a sticky
        header on some layouts, where a native click lands on the header and the
        tab body is never requested.
        """
        for selector in selectors.DOCS_TAB_SELECTORS:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
            except WebDriverException:
                continue
            for element in elements:
                try:
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", element
                    )
                    self.driver.execute_script("arguments[0].click();", element)
                    return True
                except WebDriverException:
                    continue
        return False

    def _await_attachments(self, timeout: int, expected: int | None) -> int:
        """Wait for the tab body's anchors, and return how many there are.

        Stops early once the badge's number has been reached — waiting the full
        timeout after every anchor has arrived is time a sweep cannot spare.
        """
        best = 0

        def _ready(_driver) -> bool:
            nonlocal best
            best = max(best, self._attachment_count())
            return bool(best) and (expected is None or best >= expected)

        try:
            WebDriverWait(self.driver, timeout, poll_frequency=DOC_POLL).until(_ready)
        except (TimeoutException, WebDriverException):
            pass
        return best

    def _extract_agency(self, link: str = "") -> str:
        """The issuing agency from the detail page, or "" if none is labelled.

        Best-effort by design: it feeds the export's `Niche` column for the
        member-agency sweep, whose rows belong to no niche, and a bid whose
        agency cannot be read is still a bid worth exporting under
        `member_agencies.UNKNOWN_AGENCY`.
        """
        try:
            value = self.driver.execute_script(
                self._JS_READ_AGENCY, selectors.DETAIL_FIELD, list(AGENCY_FIELD_LABELS)
            )
        except WebDriverException as exc:
            logger.info(
                "[run %s] could not read the agency (%s)%s",
                self.run_id, exc.__class__.__name__, f" for {link}" if link else "",
            )
            return ""
        return (value or "").strip() if isinstance(value, str) else ""

    def _scrape_detail(self, link: str) -> dict[str, Any]:
        """Load a solicitation's detail page and read its fields.

        A load timeout is logged with the link rather than swallowed: it is the
        one signal that the fields about to come back empty are a failure and not
        an empty solicitation.
        """
        self.driver.get(link)
        try:
            self.wait(DETAIL_TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selectors.DETAIL_FIELD))
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
        # The documents sit behind the same wall as the fields do, so there is
        # nothing to count — and 0 is the honest answer for what we can reach.
        if self.count_documents:
            record["documents_count"] = "0"
        return record

    def _page_heading(self) -> str:
        """The solicitation heading the acknowledgement page still renders."""
        try:
            for element in self.driver.find_elements(By.CSS_SELECTOR, selectors.DETAIL_HEADING):
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

        It renders over the acknowledgement dialog's button bar, the sidebar's
        Apply buttons and the pagination bar alike, so a click meant for any of
        them lands on the banner instead — silently, as an intercepted click.

        Best-effort in the strongest sense: this is tidying, and nothing it can
        run into is worth failing a run over, so **every** exception is
        swallowed rather than only WebDriverException. It is called from the
        middle of `open_filtered_session`, where anything raised would abort the
        whole filtered session over a banner that may not even be there.
        """
        try:
            for button in self.driver.find_elements(By.CSS_SELECTOR, COOKIE_ACCEPT_BUTTON):
                if button.is_displayed():
                    self.driver.execute_script("arguments[0].click();", button)
                    logger.info("[run %s] dismissed the cookie banner", self.run_id)
                    return
        except Exception:  # noqa: BLE001 — tidying must never fail a run
            logger.debug("[run %s] cookie banner check failed", self.run_id, exc_info=True)

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
                EC.presence_of_element_located((By.CSS_SELECTOR, selectors.DETAIL_FIELD))
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

    # -- orchestration ------------------------------------------------------

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        self._save_run_row()
        try:
            # HEADLESS_MODE True (production) hands the decision to the run's own
            # `live_preview` flag; False forces a visible window for every run.
            live_preview = bool((run_manager.get_run(self.run_id) or {}).get("live_preview"))
            headed = live_preview or not HEADLESS_MODE
            logger.info("[JOB INITIALIZED]: Portal: BidNet Direct")
            logger.info(
                " ├── [EXECUTION MODE]: %s (Live Preview: %s)",
                "Visible browser" if headed else "Headless Background Run",
                "ON" if live_preview else "OFF",
            )
            logger.info(
                " ├── [FILTERS APPLIED]: %s",
                "date panels active" if APPLY_DATE_FILTERS else "date panels BYPASSED",
            )
            logger.info(
                " └── [STATUS]: %s",
                "browser window open for this run"
                if headed else "scraper running in silent background context...",
            )
            self.start_driver(headless=None if HEADLESS_MODE else False)
            if not HEADLESS_MODE:
                logger.info(
                    "[LIVE DEBUG]: headed mode is ON for EVERY run (HEADLESS_MODE=False, "
                    "%.1fs pauses). Set HEADLESS_MODE=True in %s for production.",
                    DEBUG_PAUSE_SECONDS,
                    "app/scrapers/bidnet/scraper.py",
                )
            self.login()

            terms = self.search_terms
            keywords = [t.term for t in terms]
            codes = [t.term for t in terms if t.kind == KIND_NIGP]
            logger.info(
                "[run %s] niche %r: %s search term(s) — %s keyword(s) then %s NIGP "
                "code(s), searched one at a time",
                self.run_id, self.niche_label, len(terms), len(terms) - len(codes), len(codes),
            )

            # PHASE 0 — filter the session once, before a single term is typed.
            # Everything below inherits it; nothing below re-applies it unless a
            # check finds it gone.
            if terms:
                self.open_filtered_session()

            # PHASE 1 — search every term of the niche in turn, collecting
            # solicitation links. Deduplicated by solicitation id across the
            # whole run, so a bid surfaced by five keywords and two NIGP codes is
            # opened and downloaded once; each entry remembers every term that
            # found it, and they all reach the `Matched Keyword` column.
            bids: dict[str, dict] = {}
            for index, search_term in enumerate(terms, start=1):
                keyword = search_term.term
                progress = f"{index}/{len(terms)}"
                run_manager.update_run(
                    self.run_id,
                    keyword=keyword,
                    keyword_progress=progress,
                    search_kind=search_term.kind,
                )
                try:
                    # A long sequential run outlives BidNet's session, which
                    # surfaces as a redirect to the login page rather than as a
                    # timeout — no amount of waiting recovers it. Signing in
                    # again also loses the sidebar, which is why the filter check
                    # comes after it and not before.
                    self.ensure_logged_in()
                    # A no-op in the ordinary case: the filters applied before
                    # the first keyword are still on the page. It re-establishes
                    # them only after something navigated away — a re-login, or a
                    # keyword that failed partway through.
                    self._ensure_filters_live()
                    # No reload between keywords: reloading is what would clear
                    # the filters. The new term replaces the old one in the box,
                    # and the search carries the session's filters with it.
                    self.search(keyword)
                    if not self._ensure_first_result_page():
                        # The results could not be brought back to page 1, so
                        # harvesting here would silently skip everything before
                        # it. Reload, re-filter, search again — the old
                        # per-keyword cost, paid only when it is needed.
                        self._filters_live = False
                        self._ensure_filters_live()
                        self.search(keyword)

                    # Fast-fail on an empty search. The group tab reports the hit
                    # count directly, so a keyword that matched nothing costs one
                    # search instead of a grouping click plus a full element wait
                    # for rows that never arrive. `None` means the count could
                    # not be read — carry on rather than risk skipping a keyword
                    # that does have bids.
                    count = self.result_count()
                    logger.info(
                        '[run %s] [SEARCH EXECUTING]: (%s) Niche: %s | Input Type: %s '
                        '| Term: "%s"',
                        self.run_id, progress, self.niche_label, search_term.label, keyword,
                    )
                    logger.info(
                        "[run %s]  ├── [PORTAL DETECTED]: %s matching bid(s) reported "
                        "by the Member Agency group.",
                        self.run_id, _shown(count),
                    )
                    if count == 0:
                        # "No results" means no results *under the session's
                        # filters* — the search the portal answered was already
                        # narrowed by them, so this is not evidence the term
                        # matches nothing on BidNet at all.
                        logger.info(
                            "[run %s]  └── [RESULT]: 0 total bids found (0 new, 0 "
                            "duplicates skipped). %s unique solicitation(s) queued. No "
                            "results for this term under the run's filters (%s); moving "
                            "to the next.",
                            self.run_id, len(bids), self.filters.summary(),
                        )
                        self._empty_keywords.append(keyword)
                        run_manager.update_run(
                            self.run_id, keywords_without_results=list(self._empty_keywords)
                        )
                        continue

                    self.filter_member_agency()
                    # Before a row is read: ask the grid for as many rows a page
                    # as it offers. Re-checked per keyword rather than once per
                    # run because each search re-renders the grid, and a grid
                    # that came back at 25 would quietly quadruple the page
                    # transitions this exists to avoid.
                    self.set_page_size()
                    # Read-only in the ordinary case: the session's filters are
                    # confirmed still in force for this search, and only
                    # re-applied if they are not. Deliberately *after* the page
                    # size, so the last thing to re-run the search is also the
                    # last thing verified.
                    self.confirm_filters_active(keyword)
                    harvest = self.collect_links()
                except StopRequested:
                    raise
                except (TimeoutException, WebDriverException) as exc:
                    # One bad keyword must not cost the other 20 — record it and
                    # carry on to the next search. Where the failure left the
                    # browser is unknown, so the filters are treated as lost and
                    # the next keyword re-establishes them from a fresh page.
                    self._filters_live = False
                    run_manager.add_error(
                        self.run_id, f"search failed for {keyword}: {exc.__class__.__name__}"
                    )
                    self.screenshot(f"search_{index}")
                    continue

                self._rows_detected += harvest.rows_detected
                self._rows_parsed += harvest.rows_parsed
                self._rows_failed += harvest.rows_failed

                # DEDUPLICATION, round one — by solicitation id, across every
                # term the run has searched so far. A bid an earlier keyword
                # already queued is not queued again by a later NIGP code (or
                # the other way round); it only gains that term in its
                # `Matched Keyword` column. This is the round that matters for
                # cost: a bid queued twice is a detail page opened twice.
                new_links = 0
                for link in harvest.links:
                    bid_id = self._bid_key(link)
                    entry = bids.get(bid_id)
                    if entry is None:
                        new_links += 1
                        bids[bid_id] = {"link": link, "terms": [keyword]}
                        continue
                    self._link_duplicates += 1
                    if keyword not in entry["terms"]:
                        entry["terms"].append(keyword)
                    logger.debug(
                        "[run %s] [DUPLICATE SKIPPED]: Bid %r already found by %s — "
                        "not queued again for %s %r",
                        self.run_id, bid_id, ", ".join(entry["terms"][:-1]) or "an earlier term",
                        search_term.label.lower(), keyword,
                    )
                run_manager.update_run(self.run_id, bids_found=len(bids))

                duplicates = len(harvest.links) - new_links
                logger.info(
                    "[run %s]  ├── [PARSED SUCCESS]: %s/%s row(s) converted to links.",
                    self.run_id, harvest.rows_parsed, harvest.rows_detected,
                )
                logger.info(
                    "[run %s]  ├── [POST-FILTER]: %s retained (%s dropped: %s unreadable, "
                    "%s repeated across pages).",
                    self.run_id, len(harvest.links), harvest.rows_dropped,
                    harvest.rows_failed, harvest.duplicates,
                )
                logger.info(
                    "[run %s]  └── [RESULT]: %s total bids found (%s new, %s duplicates "
                    "skipped). %s unique solicitation(s) queued.",
                    self.run_id, len(harvest.links), new_links, duplicates, len(bids),
                )

                # The portal said there were bids and not one row survived. Said
                # once per keyword, at the point it happens, so the stage that
                # lost them is named instead of inferred from an empty file —
                # unless a verified date filter is what removed them, which is
                # the filter working rather than the pipeline leaking.
                if count and not harvest.links:
                    if getattr(self, "_dates_verified", False):
                        logger.info(
                            "[run %s] keyword %r: %s bid(s) matched the keyword but "
                            "none fall inside the requested date window — filtered "
                            "out, not lost.",
                            self.run_id, keyword, count,
                        )
                    else:
                        message = (
                            f"keyword {keyword!r}: the portal reported {count} bid(s) but "
                            f"none were collected — {harvest.rows_detected} row(s) rendered, "
                            f"{harvest.rows_failed} unparseable. This keyword contributes "
                            f"nothing to the export."
                        )
                        logger.error("[run %s] %s", self.run_id, message)
                        run_manager.add_error(self.run_id, message)

            # PHASE 2 — open every distinct solicitation once and read its
            # fields. One page load each: nothing is downloaded.
            self.set_step("collecting_bids")
            all_records: list[dict] = []
            for index, (bid_id, entry) in enumerate(bids.items()):
                link, matched = entry["link"], entry["terms"]
                record: dict[str, Any] | None = {
                    "reference_number": None, "title": None, "error": None,
                }
                try:
                    record = self.process_bid(link)
                except StopRequested:
                    raise
                except (TimeoutException, WebDriverException) as exc:
                    record["error"] = str(exc)[:300]
                    run_manager.add_error(self.run_id, f"bid failed: {exc.__class__.__name__}")
                    self.screenshot(f"bid_{index}")
                if record is None:
                    # Skipped by the close-date filter.
                    continue
                record["matched_keyword"] = ", ".join(matched)
                record["niche"] = self.niche_label
                # DEDUPLICATION, round two — on the reference number now that the
                # detail page has been read. Nothing is written to the master
                # list without passing this.
                if not self._claim_bid(bid_id, record, matched):
                    continue
                run_manager.add_bid_result(self.run_id, record)
                all_records.append(record)

            logger.info("[run %s] %s unique solicitations from %s search(es) "
                        "(%s keyword, %s NIGP code)",
                        self.run_id, len(bids), len(terms), len(terms) - len(codes), len(codes))

            # Did the date filter reach every keyword it was meant to?
            #
            # This is reconciled rather than assumed because the run record used
            # to answer it from the *first* keyword alone (`_sidebar_report` is
            # written once), so a filter that applied early and missed later
            # looked identical to one that worked throughout — while letting
            # out-of-window bids into the export. Counted per keyword, a miss is
            # now impossible to mistake for a clean run.
            attempted = self._dates_applied_for + self._dates_missed_for
            if attempted:
                logger.info(
                    "[DATE FILTER] run %s | applied to %d of %d searched keyword(s): %s",
                    self.run_id, len(self._dates_applied_for), len(attempted),
                    self.filters.summary(),
                )
                run_manager.update_run(
                    self.run_id,
                    dates_applied_keywords=list(self._dates_applied_for),
                    dates_missed_keywords=list(self._dates_missed_for),
                )
            if self._dates_missed_for:
                message = (
                    f"The date filter did not apply to "
                    f"{len(self._dates_missed_for)} of {len(attempted)} searched "
                    f"keyword(s): {', '.join(self._dates_missed_for)}. Bids found by "
                    f"those keywords are NOT filtered by date, so this export may "
                    f"contain solicitations outside "
                    f"{self.filters.summary()}."
                )
                logger.error("[run %s] %s", self.run_id, message)
                run_manager.add_error(self.run_id, message)

            # How well the session held its filters. Empty is the expected
            # result — applied once, kept throughout — and a long list is the
            # signal that BidNet is *not* carrying them across searches on this
            # account, which is worth knowing even though the run repaired
            # itself: every entry cost the keyword a full set of extra postbacks.
            if self._filters_reapplied_for:
                logger.warning(
                    "[run %s] [SESSION FILTER]: the sidebar had to be re-applied for "
                    "%d of %d keyword(s) — %s. The filters did not survive those "
                    "searches on their own.",
                    self.run_id, len(self._filters_reapplied_for), len(terms),
                    ", ".join(self._filters_reapplied_for),
                )
                run_manager.update_run(
                    self.run_id, filters_reapplied_keywords=list(self._filters_reapplied_for)
                )

            # What the deduplication engine actually removed. Reported because a
            # niche whose codes only ever re-find what its keywords already found
            # is a real finding about the catalog — the codes cost a search each
            # and added nothing — and it is invisible from the export, which by
            # construction contains no duplicates at all.
            logger.info(
                "[DEDUPLICATION] run %s | unique bid ids: %d | repeat sightings "
                "across terms (not queued again): %d | records dropped after "
                "extraction as the same solicitation: %d",
                self.run_id, len(self._seen_bid_ids), self._link_duplicates,
                self._duplicates_skipped,
            )
            run_manager.update_run(
                self.run_id,
                unique_bid_ids=len(self._seen_bid_ids),
                duplicates_skipped=self._duplicates_skipped,
            )

            # The whole run as one funnel, stage by stage. Read top to bottom it
            # names the stage that lost the bids: rows the portal rendered, rows
            # parsed into links, links queued, records built, records exported.
            logger.info(
                "[FUNNEL] run %s | rows rendered: %d | rows parsed: %d | rows unparseable: %d "
                "| unique solicitations queued: %d | records built: %d | exported: %d",
                self.run_id, self._rows_detected, self._rows_parsed, self._rows_failed,
                len(bids), len(all_records), len(all_records),
            )
            # An empty export with a non-empty portal is a pipeline failure, not
            # an empty niche, and it says which stage dropped them.
            if self._rows_detected and not all_records:
                stage = (
                    "every row failed to parse (the results table's markup has "
                    "changed — see the per-row warnings above)"
                    if self._rows_parsed == 0
                    else "links were collected but no record survived phase 2"
                )
                message = (
                    f"Nothing was exported although BidNet rendered "
                    f"{self._rows_detected} result row(s): {stage}."
                )
                logger.error("[run %s] %s", self.run_id, message)
                run_manager.add_error(self.run_id, message)

            # Reconciliation, printed before anything is saved: every collected
            # solicitation is accounted for, and the export count is the sum of
            # the parts. If these ever disagree, the run says so here rather than
            # leaving it to be noticed in the spreadsheet days later.
            by_status = Counter(r.get("status") or STATUS_OK for r in all_records)
            fallback = by_status[STATUS_PARTIAL] + by_status[STATUS_FAILED]
            logger.info(
                "[SUMMARY] run %s | Scraped: %d | Fully extracted: %d | Failed/Fallback: %d "
                "| Acknowledgement required: %d | Final Export Count: %d "
                "| Skipped (closing soon): %d | Skipped (duplicate): %d",
                self.run_id, len(bids), by_status[STATUS_OK], fallback,
                by_status[STATUS_ACK_REQUIRED], len(all_records),
                self._skipped_closing_soon, self._duplicates_skipped,
            )
            expected = len(bids) - self._skipped_closing_soon - self._duplicates_skipped
            if len(all_records) != expected:
                logger.error(
                    "[SUMMARY] run %s | MISMATCH: %d collected - %d closing soon - "
                    "%d duplicate = %d expected, but %d record(s) are being exported",
                    self.run_id, len(bids), self._skipped_closing_soon,
                    self._duplicates_skipped, expected, len(all_records),
                )
            run_manager.update_run(
                self.run_id,
                rows_detected=self._rows_detected,
                rows_parsed=self._rows_parsed,
                rows_unparseable=self._rows_failed,
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

            # One master spreadsheet for the whole run, at the root of its folder.
            self._write_master_excel(all_records)

            if self._empty_keywords:
                logger.info(
                    "[run %s] %s of %s search term(s) returned no bids: %s",
                    self.run_id, len(self._empty_keywords), len(terms),
                    ", ".join(self._empty_keywords),
                )
                run_manager.add_warning(
                    self.run_id,
                    f"{len(self._empty_keywords)} of {len(terms)} search terms matched "
                    f"nothing on BidNet under this run's filters "
                    f"({self.filters.summary()}): {', '.join(self._empty_keywords)}",
                )
            # Every keyword came back empty — the searches worked, the portal
            # simply has nothing for this niche right now.
            run_manager.update_run(
                self.run_id, no_results=len(self._empty_keywords) == len(terms)
            )

            # Surface the close-date filter's effect (see app/core/closing_filter).
            # Only when it ran: the console renders a "closing-date filter"
            # note whenever a run reports one, and a run that dropped nothing
            # must not claim to have filtered.
            if APPLY_CLOSE_DATE_FILTER:
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
            else:
                logger.info(
                    "[run %s] no close-date filter — every solicitation the portal "
                    "returned was kept (%s)", self.run_id, len(all_records),
                )

            # Persist every scraped solicitation in one transaction (mirrors
            # MyFlorida). The DB stays globally de-duplicated per run (by reference
            # number); the niche+tier split lives in the folders and their Excels.
            # Best-effort: a DB failure must not fail the run.
            run = run_manager.get_run(self.run_id) or {"run_id": self.run_id}
            try:
                stored = export.save_bids(run, all_records)
                run_manager.update_run(self.run_id, bids_stored_in_db=stored)
                # The last stage that can lose rows: the run-level Excel in the
                # archive is regenerated from these DB rows, so anything that
                # does not land here is absent from the file the client opens.
                logger.info(
                    "[run %s] [DB SAVE]: %d record(s) in memory → %d row(s) stored.",
                    self.run_id, len(all_records), stored,
                )
                if len(all_records) != stored:
                    message = (
                        f"{len(all_records) - stored} of {len(all_records)} scraped "
                        f"record(s) were not stored in the database and will be missing "
                        f"from the packaged spreadsheet (duplicate reference numbers "
                        f"within this run — see the warning above)."
                    )
                    logger.error("[run %s] %s", self.run_id, message)
                    run_manager.add_error(self.run_id, message)
            except Exception:  # noqa: BLE001 — DB issues shouldn't abort the run
                logger.exception("[run %s] DB save failed", self.run_id)
                run_manager.add_error(self.run_id, "db save failed (see logs)")

            run_manager.update_run(self.run_id, excel_exported=True)

            # Package the run: the day's session root — one folder per niche
            # run today, each holding that niche's spreadsheet — into one ZIP.
            self.set_step("packaging_results")
            archive_run(self.run_id)

            run_manager.update_run(self.run_id, status="completed", step="done")
            # Email/S3 notification on successful completion.
            notify_scrape_completion(self.run_id, "bidnet", len(all_records))
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
            run_manager.add_error(self.run_id, str(exc)[:500])
            run_manager.update_run(self.run_id, status="failed", step="failed")
        finally:
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
    """Run one niche: resolve its search terms from the database, then search
    each in turn — every keyword, then every NIGP code. Resolved here rather than
    passed in so the run always uses the catalog as it stands when the browser
    actually starts."""
    session = SessionLocal()
    try:
        niche = niches.get_niche(session, niche_key)
        terms = niches.search_terms_for(session, niche_key)
        label = niche.label if niche else niche_key
    finally:
        session.close()

    if not terms:
        run_manager.add_error(run_id, f"niche '{label}' has no search terms")
        run_manager.update_run(run_id, status="failed", step="failed")
        return

    BidnetScraper(run_id, terms, filters, niche_label=label).run()
