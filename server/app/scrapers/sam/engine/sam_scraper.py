"""
SAM.gov Procurement Scraper
Description:
  Scrapes active solicitations from SAM.gov filtered by a user-supplied date.
  Applies Response Date + Updated Date URL filters, plus a new Partial Small
  Business Set-Aside (SBP / FAR 19.5).

Extraction fields (9):
  1. Notice Title
  2. Notice ID
  3. Department/Ind. Agency   → skip if contains "Department of Defense"
  4. Description
  5. Subtier                  → skip if contains "Department of Defense"
  6. Updated Date             → skip if version count > 1  (keep 0 or 1 only)
  7. Date Offers Due
  8. Published Date
  9. Office                   → skip if contains "DLA" / "Defense Logistics Agency"

Card-level pre-filters:
  • Forbidden titles  (rfi, market research, foods, meal, survey)
  • Version count > 1
  • Updated Date < user date_filter
"""

import os
import shutil
import time
import logging
from datetime import datetime
from pathlib import Path

import yaml
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from bs4 import BeautifulSoup

try:
    from .documents import download_attachments as _download_attachments
    from .text_extractor import build_full_text as _build_full_text
    from .utils import (
        parse_any_date, looks_like_date, clean_updated_date,
        matches_date_range, check_updated_date_rule,
        is_valid_title, should_skip_bid, find_field, save_debug,
        DATE_PATTERN, ISO_DATE_RE, SLASH_DATE_RE, FULL_MONTH_RE,
    )
    from .exceptions import StopScraping, check_stop, smart_sleep
    from .extractors import (
        get_field as _get_field_standalone,
        regex_from_page_text as _regex_from_page_text_standalone,
        regex_date_from_page as _regex_date_from_page_standalone,
        extract_description as _extract_description_standalone,
    )
    from .csv_handler import (
        resolve_output_dir as _resolve_output_dir,
        get_csv_filename as _get_csv_filename,
        init_csv as _init_csv,
        append_row as _append_row,
        save_csv as _save_csv,
    )
    from .browser import (
        setup_driver as _setup_driver,
        wait_for_page_load as _wait_for_page_load,
        wait_for_angular as _wait_for_angular,
        random_delay as _random_delay,
    )
    from .navigation import (
        build_page_url as _build_page_url,
        verify_on_correct_page as _verify_on_correct_page,
        click_next_page as _click_next_page,
        apply_ui_date_filters as _apply_ui_date_filters,
        apply_naics_filter as _apply_naics_filter,
        is_past_date_window as _is_past_date_window,
    )
    from .page_parser import (
        get_links_from_current_page as _get_links_from_current_page,
    )
except ImportError:
    from documents import download_attachments as _download_attachments
    from text_extractor import build_full_text as _build_full_text
    from utils import (
        parse_any_date, looks_like_date, clean_updated_date,
        matches_date_range, check_updated_date_rule,
        is_valid_title, should_skip_bid, find_field, save_debug,
        DATE_PATTERN, ISO_DATE_RE, SLASH_DATE_RE, FULL_MONTH_RE,
    )
    from extractors import (
        get_field as _get_field_standalone,
        regex_from_page_text as _regex_from_page_text_standalone,
        regex_date_from_page as _regex_date_from_page_standalone,
        extract_description as _extract_description_standalone,
    )
    from csv_handler import (
        resolve_output_dir as _resolve_output_dir,
        get_csv_filename as _get_csv_filename,
        init_csv as _init_csv,
        append_row as _append_row,
        save_csv as _save_csv,
    )
    from browser import (
        setup_driver as _setup_driver,
        wait_for_page_load as _wait_for_page_load,
        wait_for_angular as _wait_for_angular,
        random_delay as _random_delay,
    )
    from navigation import (
        build_page_url as _build_page_url,
        verify_on_correct_page as _verify_on_correct_page,
        click_next_page as _click_next_page,
        apply_ui_date_filters as _apply_ui_date_filters,
        apply_naics_filter as _apply_naics_filter,
        is_past_date_window as _is_past_date_window,
    )
    from page_parser import (
        get_links_from_current_page as _get_links_from_current_page,
    )

# Resolve the directory this file lives in (server/sam/)
_SAM_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Bootstrap logging from config.yml before basicConfig is called
# ---------------------------------------------------------------------------
def _get_sam_log_config() -> dict:
    # Try dedicated sam/config.yml first, then fall back to root config/config.yml
    for cfg_file in (_SAM_DIR / "config.yml", Path.cwd() / "config" / "config.yml"):
        if cfg_file.exists():
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    raw = yaml.safe_load(f)
                return raw.get("sam", {}).get("logging", {})
            except Exception:
                pass
    return {}


_log_cfg = _get_sam_log_config()
_log_file = _log_cfg.get("log_file", "logs/sam_scraper.log")
os.makedirs(os.path.dirname(_log_file) or ".", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format=_log_cfg.get("format", "%(asctime)s - %(levelname)s - %(message)s"),
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_log_file),
    ],
)
logger = logging.getLogger(__name__)


# ===========================================================================
# SAMGovScraper
# ===========================================================================
class SAMGovScraper:
    """
    Scrapes SAM.gov solicitations filtered by date.
    All tuneable values come from config.yml under the 'sam:' key.
    """

    def __init__(
        self,
        headless: bool = True,
        date_filter: str = None,   # kept for backward-compat; treated as date_from
        date_to: str = None,
        naics_codes: list[str] | None = None,
        award_notice: bool = False,
    ):
        self.award_notice = award_notice     # must be set BEFORE _load_config() reads it
        self._load_config()

        self.headless     = headless
        self.date_filter  = date_filter      # YYYY-MM-DD  (from / start of range)
        self.date_to      = date_to          # YYYY-MM-DD  (to   / end   of range)
        self.naics_codes  = naics_codes or []  # list of 6-digit NAICS codes to filter

        # Parsed datetime objects
        self.filter_date_from = None        # start of range
        self.filter_date_to   = None        # end   of range (defaults to today)
        self.filter_date_obj  = None        # backward-compat alias = filter_date_from

        self.data = []
        self._output_filename = None
        self._csv_filepath    = None
        self._stop_event      = None   # optional threading.Event for graceful stop
        self.skip_csv         = False  # set True to skip all file I/O (DB-only mode)
        self._on_bid_extracted = None  # optional callback(dict) — called per saved bid

        fmt = self._date_cfg.get("filter_date_format", "%Y-%m-%d")

        # ── Parse from-date ──────────────────────────────────────────────
        if self.date_filter:
            try:
                self.filter_date_from = datetime.strptime(self.date_filter, fmt)
                self.filter_date_obj  = self.filter_date_from   # backward-compat alias
            except Exception as e:
                logger.warning(f"Invalid date_filter '{self.date_filter}'. Filter disabled. {e}")

        # ── Parse to-date; default to today if from-date is set ──────────
        if self.date_to:
            try:
                self.filter_date_to = datetime.strptime(self.date_to, fmt)
            except Exception as e:
                logger.warning(f"Invalid date_to '{self.date_to}'. Defaulting to today. {e}")

        if self.filter_date_from and self.filter_date_to is None:
            # No to-date → treat today as the upper bound
            self.filter_date_to = datetime.now().replace(
                hour=23, minute=59, second=59, microsecond=0
            )

        # ── Log the active filter ────────────────────────────────────────
        if self.filter_date_from:
            from_str = self.filter_date_from.strftime("%Y-%m-%d")
            to_str   = self.filter_date_to.strftime("%Y-%m-%d") if self.filter_date_to else "today"
            if from_str == to_str or (
                self.filter_date_to and
                self.filter_date_from.date() == self.filter_date_to.date()
            ):
                logger.info(f"Date filter active: Published Date = {from_str} (exact match)")
            else:
                logger.info(f"Date range filter active: Published Date {from_str} to {to_str}")

        self.setup_driver()

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------
    def _load_config(self):
        # Try dedicated sam/config.yml first, then fall back to root config/config.yml
        cfg_file = _SAM_DIR / "config.yml"
        if not cfg_file.exists():
            cfg_file = Path.cwd() / "config" / "config.yml"
        if not cfg_file.exists():
            raise FileNotFoundError(f"config.yml not found at {cfg_file}")

        with open(cfg_file, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        self._cfg = raw.get("sam", {})
        urls_sam = raw.get("urls", {}).get("sam", {})

        if self.award_notice:
            # Award Notice mode: use dedicated URL that contains ONLY Award Notice
            # as the notice type (no Solicitation or Combined Synopsis)
            self.base_url = urls_sam.get("award_notice_base_url", "")
            if not self.base_url:
                raise ValueError("urls.sam.award_notice_base_url is missing from config.yml")
        else:
            self.base_url = urls_sam.get("base_url", "")
            if not self.base_url:
                raise ValueError("urls.sam.base_url is missing from config.yml")

        # temp_docs directory — one sub-folder per notice ID
        self._temp_docs_dir = _SAM_DIR / "temp_docs"
        self._temp_docs_dir.mkdir(exist_ok=True)

        # Convenience shortcuts
        self._timeouts       = self._cfg.get("timeouts", {})
        self._selectors      = self._cfg.get("selectors", {})
        self._filtering      = self._cfg.get("filtering", {})
        self._skip_cond      = self._cfg.get("skip_conditions", {})
        self._date_cfg       = self._cfg.get("date_parsing", {})
        self._scraping       = self._cfg.get("scraping", {})
        self._csv_cfg        = self._cfg.get("csv", {})
        self._field_ids      = self._cfg.get("detail_field_ids", {})
        self._desc_selectors = self._cfg.get("description_selectors", [])
        self._desc_label     = self._cfg.get("description_heading_label", "Description")
        self._debug_cfg           = self._cfg.get("debug", {})
        self._url_date_params     = self._cfg.get("url_date_params", {})
        self._date_filter_ui_cfg  = self._cfg.get("date_filter_ui", {})

    # ------------------------------------------------------------------
    # Chrome driver
    # ------------------------------------------------------------------
    def setup_driver(self):
        """Delegates to browser.setup_driver."""
        self.driver = _setup_driver(self.headless, self._cfg)

    # ------------------------------------------------------------------
    # URL builder
    # ------------------------------------------------------------------
    def _build_page_url(self, page: int) -> str:
        """Delegates to navigation.build_page_url."""
        # When award_notice=True, base_url is already the Award Notice-only URL,
        # so award_notice_param must NOT be appended (it would duplicate the filter).
        return _build_page_url(
            self.base_url, page, self.naics_codes,
            self.filter_date_from, self.filter_date_to,
            self._url_date_params, self.filter_date_obj,
            award_notice=False,       # never append param; base_url already encodes the intent
            award_notice_param="",
        )

    # ------------------------------------------------------------------
    # Page-number verifier
    # ------------------------------------------------------------------
    def _verify_on_correct_page(self, expected_page: int) -> bool:
        """Delegates to navigation.verify_on_correct_page."""
        return _verify_on_correct_page(self.driver, expected_page)

    # ------------------------------------------------------------------
    # Next-page navigation
    # ------------------------------------------------------------------
    def _click_next_page(self, current_page: int) -> bool:
        """Delegates to navigation.click_next_page."""
        return _click_next_page(
            self.driver, current_page, self._timeouts, self._selectors
        )

    # ------------------------------------------------------------------
    # UI date-range filter
    # ------------------------------------------------------------------
    def _apply_ui_date_filters(self) -> bool:
        """Delegates to navigation.apply_ui_date_filters."""
        return _apply_ui_date_filters(
            self.driver, self.filter_date_from,
            self.filter_date_to, self._date_filter_ui_cfg,
        )

    # ------------------------------------------------------------------
    # NAICS code filter
    # ------------------------------------------------------------------
    def _apply_naics_filter(self):
        """Delegates to navigation.apply_naics_filter."""
        _apply_naics_filter(self.driver, self.naics_codes)

    # ------------------------------------------------------------------
    # Timing helpers
    # ------------------------------------------------------------------
    def _random_delay(self):
        """Delegates to browser.random_delay."""
        _random_delay(self._timeouts)

    # ------------------------------------------------------------------
    # Date-window boundary detector
    # ------------------------------------------------------------------
    def _is_past_date_window(self) -> bool:
        """Delegates to navigation.is_past_date_window."""
        return _is_past_date_window(
            self.driver, self.filter_date_from,
            self._date_cfg, self._selectors, self._DATE_PATTERN,
        )

    def _wait_for_page_load(self):
        """Delegates to browser.wait_for_page_load."""
        _wait_for_page_load(self.driver, self._timeouts)

    def _wait_for_angular(self):
        """Delegates to browser.wait_for_angular."""
        _wait_for_angular(self.driver)

    # ------------------------------------------------------------------
    # Card-level filters
    # ------------------------------------------------------------------
    def _check_updated_date_rule(self, date_str: str) -> bool:
        """Delegates to utils.check_updated_date_rule with config threshold."""
        threshold = self._filtering.get("version_count_threshold", 1)
        return check_updated_date_rule(date_str, threshold)

    def _matches_published_date(self, date_str: str) -> bool:
        """Delegates to utils.matches_date_range."""
        return matches_date_range(date_str, self.filter_date_from, self.filter_date_to)

    def _clean_updated_date(self, date_str: str) -> str:
        """Delegates to utils.clean_updated_date."""
        return clean_updated_date(date_str)

    def _parse_any_date(self, s: str) -> str:
        """Delegates to utils.parse_any_date."""
        return parse_any_date(s)

    def _looks_like_date(self, s: str) -> bool:
        """Delegates to utils.looks_like_date."""
        return looks_like_date(s)

    def _should_skip_bid(self, data: dict) -> tuple[bool, str]:
        """Delegates to utils.should_skip_bid."""
        return should_skip_bid(data, self._skip_cond)

    # Class-level date patterns — point to the module-level constants from utils
    _DATE_PATTERN  = DATE_PATTERN
    _ISO_DATE_RE   = ISO_DATE_RE
    _SLASH_DATE_RE = SLASH_DATE_RE

    # ------------------------------------------------------------------
    # Search-results page – candidate link extraction
    # ------------------------------------------------------------------
    def get_links_from_current_page(self) -> list:
        """Delegates to page_parser.get_links_from_current_page."""
        return _get_links_from_current_page(
            self.driver, self._selectors, self._date_cfg,
            self._filtering, self._skip_cond,
            self.filter_date_obj, self.filter_date_from,
            self.filter_date_to, self._debug_cfg,
        )

    # ------------------------------------------------------------------
    # Detail page – full field extraction
    # ------------------------------------------------------------------
    def extract_details(self, url: str, pre_updated_date: str = "") -> dict | None:
        """
        Visit an opportunity detail page and extract all 9 required fields.
        Returns None if any skip condition is triggered.

        pre_updated_date: Updated Date already extracted from the search-results
        card (sds-field__value).  When provided the method skips the detail-page
        extraction for that field; the version check was already applied at the
        card-filter stage.
        """
        data = {
            "Notice Title":           "",
            "Notice ID":              "",
            "Department/Ind. Agency": "",
            "Description":            "",
            "Subtier":                "",
            "Updated Date":           "",
            "Date Offers Due":        "",
            "Published Date":         "",
            "Office":                 "",
        }

        try:
            check_stop(self._stop_event)
            self.driver.get(url)
            self._wait_for_page_load()
            # Wait for Angular to finish rendering field elements
            self._wait_for_angular()

            # Scroll halfway down to trigger lazy-loaded content, then back up
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
            smart_sleep(1, self._stop_event)
            self.driver.execute_script("window.scrollTo(0, 0);")
            smart_sleep(0.5, self._stop_event)

            check_stop(self._stop_event)
            self._random_delay()
            check_stop(self._stop_event)

            # Re-parse after Angular has fully rendered
            soup = BeautifulSoup(self.driver.page_source, "html.parser")

            ids = self._field_ids

            # ── Field 1: Notice Title ────────────────────────────────────
            h1 = soup.select_one("h1")
            data["Notice Title"] = h1.get_text(strip=True) if h1 else ""
            if not data["Notice Title"]:
                try:
                    el = self.driver.find_element(By.TAG_NAME, "h1")
                    data["Notice Title"] = el.text.strip()
                except Exception:
                    pass

            # ── Field 2: Notice ID ───────────────────────────────────────
            data["Notice ID"] = self._get_field(
                soup, ids.get("notice_id", "notice-id"), "Notice ID"
            )

            # ── Field 3: Department/Ind. Agency ─────────────────────────
            data["Department/Ind. Agency"] = self._get_field(
                soup, ids.get("department_agency", "department-ind-agency"),
                "Department/Ind. Agency"
            )

            # ── Field 4: Description ─────────────────────────────────────
            # Extracted BEFORE skip check so we can log full info; filtered out below if DoD
            data["Description"] = self._extract_description(soup)

            # ── Field 5: Subtier ─────────────────────────────────────────
            data["Subtier"] = self._get_field(
                soup, ids.get("sub_tier", "sub-tier"), "Sub-Tier"
            )

            # ── Field 6: Updated Date ────────────────────────────────────
            # Use the value already pulled from the search-results card when
            # available — it comes from the sds-field__value element and is
            # more reliable than re-extracting it from the detail page.
            if pre_updated_date:
                data["Updated Date"] = self._clean_updated_date(pre_updated_date)
                logger.debug(
                    f"Updated Date taken from card: {data['Updated Date']}"
                )
            else:
                # Fall back to detail-page extraction (no card value provided)
                _raw_updated = self._get_field(
                    soup, ids.get("updated_date", "updated-date"),
                    self._date_cfg.get("updated_date_card_label", "Updated Date")
                )
                # Guard: discard URL/garbage values returned by fallback strategies
                if _raw_updated and not self._looks_like_date(_raw_updated):
                    logger.debug(
                        f"Updated Date fallback returned non-date value "
                        f"'{_raw_updated[:60]}' – discarding and using regex."
                    )
                    _raw_updated = ""
                if not _raw_updated:
                    _raw_updated = self._regex_date_from_page("Updated Date")
                data["Updated Date"] = _raw_updated

            # ── Field 7: Date Offers Due ─────────────────────────────────
            # SAM.gov can show this in many formats depending on the user's
            # browser locale / timezone, e.g.:
            #   "Mar 31, 2026 5:00 PM GMT+7"
            #   "2026-03-31T17:00:00+05:30"
            #   "03/31/2026"
            # _parse_any_date() normalises all of these to "Mon D, YYYY".
            # If the primary element lookup fails we try an alternate ID and
            # then a full page-body regex scan so the field is never left
            # blank when the date IS present on the page.
            _raw_due = self._get_field(
                soup, ids.get("date_offers_due", "date-offers-date"), "Date Offers Due"
            )
            if not _raw_due:
                _raw_due = self._get_field(
                    soup, ids.get("date_offers_due_alt", "offers-due-date"), ""
                )

            # Normalise: extract just the date regardless of time/tz suffix
            _raw_due = self._parse_any_date(_raw_due)

            # Fallback: scan the full rendered page body for the date
            if not _raw_due:
                _fallback_due = self._regex_date_from_page("Date Offers Due")
                if not _fallback_due:
                    # Some pages label it "Response Date" instead
                    _fallback_due = self._regex_date_from_page("Response Date")
                if _fallback_due:
                    _raw_due = self._parse_any_date(_fallback_due)
                    if _raw_due:
                        logger.debug(f"Date Offers Due recovered via page-text fallback: {_raw_due}")

            data["Date Offers Due"] = _raw_due

            # ── Field 8: Published Date ──────────────────────────────────
            # Uses the same normalise + fallback pipeline as Date Offers Due.
            # Handles any format SAM.gov may show:
            #   "Mar 17, 2026 2:26 PM GMT+7"
            #   "2026-03-17T00:00:00+05:30"
            #   "03/17/2026"  etc.
            _raw_pub = self._get_field(
                soup, ids.get("published_date", "published-date"), "Published Date"
            )

            # Normalise to "Mon D, YYYY"
            _raw_pub = self._parse_any_date(_raw_pub)

            # Fallback: scan the full rendered page body for the date
            if not _raw_pub:
                _fallback_pub = self._regex_date_from_page("Published Date")
                if _fallback_pub:
                    _raw_pub = self._parse_any_date(_fallback_pub)
                    if _raw_pub:
                        logger.debug(f"Published Date recovered via page-text fallback: {_raw_pub}")

            data["Published Date"] = _raw_pub

            # ── Field 9: Office ──────────────────────────────────────────
            data["Office"] = self._get_field(
                soup, ids.get("office", "office"), "Office"
            )

            # ── Field 10: NAICS Code + Title ─────────────────────────────
            # DOM structure on bid detail page:
            #   <div id="naics" class="sds-field"> NAICS Code </div>
            #   <h5 aria-describedby="naics" class="value-new-line">
            #       561730 - Landscaping Services
            #   </h5>
            naics_code  = ""
            naics_title = ""
            try:
                # Strategy A: aria-describedby="naics" on <h5>
                naics_h5 = soup.find("h5", attrs={"aria-describedby": "naics"})
                if naics_h5:
                    naics_text = naics_h5.get_text(strip=True)
                else:
                    # Strategy B: Selenium fallback
                    try:
                        naics_el = self.driver.find_element(
                            By.CSS_SELECTOR, "h5[aria-describedby='naics']"
                        )
                        naics_text = naics_el.text.strip()
                    except Exception:
                        naics_text = ""

                if naics_text and " - " in naics_text:
                    parts = naics_text.split(" - ", 1)
                    naics_code  = parts[0].strip()
                    naics_title = parts[1].strip()
                elif naics_text:
                    # Might be just the code without title
                    naics_code = naics_text.strip()
            except Exception as _naics_err:
                logger.debug(f"NAICS extraction failed: {_naics_err}")

            data["NAICS Code"]  = naics_code
            data["NAICS Title"] = naics_title

            # ── Re-verify version/date rule against detail-page Updated Date
            #    (uses raw string that still contains the version count)
            if not self._check_updated_date_rule(data["Updated Date"]):
                logger.info(
                    f"[SKIP] Version count > 1: {data['Updated Date']}"
                )
                return None

            # ── Store ONLY the bare date in Updated Date column ──────────
            #    Strip "(N)" so the CSV shows e.g. "Mar 17, 2026" only.
            data["Updated Date"] = self._clean_updated_date(data["Updated Date"])

            # ── Published Date (extracted from detail page) ───────────────
            # We no longer apply a strict exact-match or empty-date rule here.
            # SAM.gov sometimes shows different dates on the card vs the
            # detail page (e.g., Mar 17 on card, Mar 16 on detail). The user
            # wants to trust the CARD's published date, which we already verified
            # in get_links_from_current_page(). We just extract what's here for
            # the CSV without throwing the bid away.
            # ── Apply DoD / DLA skip conditions ─────────────────────────
            skip, reason = self._should_skip_bid(data)
            if skip:
                logger.info(f"[SKIP] {reason} | {data['Notice Title']}")
                return None

            # ── Download attachments ─────────────────────────────────────
            # Scroll to the Attachments/Links section so Angular renders it
            try:
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.5)
            except Exception:
                pass

            notice_id_for_dir = data.get("Notice ID") or data.get("Notice Title", "unknown")
            _download_attachments(self.driver, notice_id_for_dir, self._temp_docs_dir, stop_event=self._stop_event)

            # ── Build full text (description + extracted doc text) ───────
            docs_folder = self._temp_docs_dir / notice_id_for_dir
            data["Full Text"] = _build_full_text(data.get("Description", ""), docs_folder)
            logger.info(
                f"Full Text built: {len(data['Full Text']):,} chars "
                f"(notice {notice_id_for_dir})"
            )

            # ── Clean up downloaded files now that text is extracted ────
            if docs_folder.exists():
                try:
                    shutil.rmtree(docs_folder)
                    logger.info(f"Cleaned up temp docs: {docs_folder}")
                except Exception as exc:
                    logger.warning(f"Could not remove {docs_folder}: {exc}")

            return data

        except Exception as e:
            logger.error(f"Error extracting details from {url}: {e}")
            return None

    # ------------------------------------------------------------------
    # Multi-strategy field extractor (BS4 + Selenium fallbacks)
    # ------------------------------------------------------------------
    def _get_field(self, soup: BeautifulSoup, field_id: str, label: str) -> str:
        """Delegates to extractors.get_field."""
        return _get_field_standalone(soup, field_id, label, self.driver)

    # ------------------------------------------------------------------
    # Regex helpers for full-page text fallback
    # ------------------------------------------------------------------
    def _regex_from_page_text(self, label: str) -> str:
        """Delegates to extractors.regex_from_page_text."""
        return _regex_from_page_text_standalone(self.driver, label)

    def _regex_date_from_page(self, label: str) -> str:
        """Delegates to extractors.regex_date_from_page."""
        date_regex = self._date_cfg.get(
            "card_date_regex", r"([A-Z][a-z]{2}\s\d{1,2},\s\d{4})"
        )
        return _regex_date_from_page_standalone(self.driver, label, date_regex)

    # ------------------------------------------------------------------
    # Description extractor
    # ------------------------------------------------------------------
    def _extract_description(self, soup: BeautifulSoup) -> str:
        """Delegates to extractors.extract_description."""
        return _extract_description_standalone(
            self.driver, self._desc_selectors, self._desc_label
        )

    # ------------------------------------------------------------------
    # Generic field fallback – text-search in soup
    # ------------------------------------------------------------------
    def _find_field(self, soup: BeautifulSoup, label: str) -> str:
        """Delegates to utils.find_field."""
        return find_field(soup, label)

    # ------------------------------------------------------------------
    # Debug helper
    # ------------------------------------------------------------------
    def _save_debug(self, filename: str):
        """Delegates to utils.save_debug."""
        save_debug(self.driver, filename)

    # ------------------------------------------------------------------
    # Instant CSV helpers — delegated to csv_handler module
    # ------------------------------------------------------------------
    def _resolve_output_dir(self) -> Path:
        """Delegates to csv_handler.resolve_output_dir."""
        return _resolve_output_dir(self._csv_cfg)

    def _init_csv(self) -> Path:
        """Delegates to csv_handler.init_csv."""
        return _init_csv(self._csv_cfg, self._output_filename)

    def _append_row(self, row: dict) -> None:
        """Delegates to csv_handler.append_row."""
        _append_row(self._csv_filepath, row, self._csv_cfg)

    # ------------------------------------------------------------------
    # Dynamic CSV filename
    # ------------------------------------------------------------------
    def get_csv_filename(self) -> str:
        """Delegates to csv_handler.get_csv_filename."""
        return _get_csv_filename(self._csv_cfg)

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------
    def run(self, max_records: int = None) -> str | None:
        if max_records is None:
            max_records = self._scraping.get("max_records", 1000)

        # ── Generate filename / init CSV (skipped in DB-only mode) ───────────
        self._output_filename = self.get_csv_filename()
        if self.skip_csv:
            self._csv_filepath = None   # no file I/O — data goes to DB via callback
        else:
            self._csv_filepath = self._init_csv()   # file exists on disk from this point

        extracted_count     = 0
        page                = 1
        scraped_urls: set   = set()
        scraped_titles: set = set()

        # ── Stopping rule ───────────────────────────────────────────────
        # We ONLY stop when SAM.gov itself has no more cards to show
        # (WebDriverWait times out = genuine end of results).
        # We NEVER stop because our client-side filters (version count,
        # DoD/DLA, Published Date) eliminated all cards on a page –
        # matching bids may exist on any later page.
        # ────────────────────────────────────────────────────────────────

        while extracted_count < max_records:
            # ── Stop check ───────────────────────────────────────────────────
            try:
                check_stop(self._stop_event)
            except StopScraping:
                logger.info(
                    f"Stop signal received - saving {extracted_count} partial "
                    f"rows and exiting."
                )
                break

            logger.info(f"-- Page {page} ------------------------------------------")

            if page == 1:
                # ── Page 1: navigate directly via URL ────────────────────
                # Only page 1 can be reached via driver.get().  SAM.gov's
                # Angular SPA redirects any direct ?page=N (N>1) back to
                # page 1 because there is no active search session yet.
                page_url = self._build_page_url(1)
                self.driver.get(page_url)
                smart_sleep(2, self._stop_event)   # Angular routing startup

                # Wait for result cards
                has_cards = False
                try:
                    WebDriverWait(
                        self.driver, self._timeouts.get("results_wait", 20)
                    ).until(
                        lambda d: d.find_elements(
                            By.CSS_SELECTOR,
                            self._selectors.get(
                                "results_container_css",
                                "sds-search-result-list, .sds-card",
                            ),
                        )
                    )
                    has_cards = True
                except Exception:
                    pass

                if not has_cards:
                    logger.info("Page 1: no cards rendered - end of results.")
                    break

                # ── Apply UI date-range filter on the first page load ─────
                # The URL params already told SAM.gov the date range server-
                # side; filling the date-picker inputs confirms the filter
                # in the Angular state so it persists across page clicks.
                self._apply_ui_date_filters()

                # ── Apply NAICS code filter ────────────────────────────────
                self._apply_naics_filter()

            else:
                # ── Page 2+: click the Next button to stay in-session ────
                # We must use the UI button so that SAM.gov's Angular router
                # knows about the existing session / cursor state.
                # _click_next_page() blocks until cards appear on the new
                # page (or returns False when the button is disabled /
                # missing = genuine end of results).
                if not self._click_next_page(page - 1):
                    break

                # Sanity-check: confirm the page indicator matches expectation
                if not self._verify_on_correct_page(page):
                    break

            # ── Card-level filtering (version / title / Published Date) ─
            candidates = self.get_links_from_current_page()
            logger.info(
                f"Page {page}: {len(candidates)} candidate(s) after card filters."
            )
            # If every card was filtered client-side, check whether we have
            # moved past the date window before deciding to continue.
            # Since SAM.gov sorts by -modifiedDate, once every card on a page
            # has Updated Date < filter date no later page can contain
            # matching bids — stop immediately instead of paging forever.
            if not candidates:
                if self._is_past_date_window():
                    break
                page += 1
                continue

            # ── Visit each candidate's detail page ──────────────────────
            for item in candidates:
                if extracted_count >= max_records:
                    break

                # ── Per-bid stop check ───────────────────────────────────
                # Checked here so Stop responds within ~1 bid (~10 s)
                # instead of waiting for all candidates on the page.
                try:
                    check_stop(self._stop_event)
                except StopScraping:
                    logger.info(
                        f"Stop signal received during bid extraction - "
                        f"saving {extracted_count} partial rows and exiting."
                    )
                    break

                bid_url   = item["url"]
                bid_title = item["title"]

                if bid_url in scraped_urls or bid_title in scraped_titles:
                    logger.info(f"[DUP] {bid_title}")
                    scraped_urls.add(bid_url)
                    continue

                logger.info(f"Scraping -> {bid_title}")

                # Open in new tab to preserve the search page's internal state
                self.driver.execute_script("window.open('');")
                self.driver.switch_to.window(self.driver.window_handles[1])

                details = None
                try:
                    check_stop(self._stop_event)
                    details = self.extract_details(
                        bid_url,
                        pre_updated_date=item.get("pre_extracted_updated_date", ""),
                    )
                except StopScraping:
                    logger.info("Stop signal received inside bid extraction - saving and exiting.")
                    break
                except Exception as _detail_err:
                    _emsg = str(_detail_err).lower()
                    # Browser was closed (manually or due to stop) — exit cleanly
                    if any(k in _emsg for k in (
                        "invalid session", "disconnected",
                        "not connected", "no such window",
                        "browser has closed",
                    )):
                        logger.warning(
                            f"Browser session lost - saving {extracted_count} "
                            f"partial rows and exiting."
                        )
                        if self._stop_event:
                            self._stop_event.set()
                        break
                    logger.warning(f"Error extracting details from {bid_url}: {_detail_err}")
                finally:
                    # Close the tab and switch back; ignore errors if session is gone
                    try:
                        self.driver.close()
                        self.driver.switch_to.window(self.driver.window_handles[0])
                    except Exception:
                        pass

                if details:
                    # Attach the card-level repeat count to the detail dict
                    # so it flows through to the DB callback and CSV row.
                    details["bid_repeat_count"] = item.get("bid_repeat_count", 0)
                    self.data.append(details)
                    if not self.skip_csv:
                        self._append_row(details)
                    if self._on_bid_extracted:
                        try:
                            self._on_bid_extracted(details)
                        except Exception as _cb_err:
                            logger.warning(f"on_bid_extracted callback failed: {_cb_err}")
                    scraped_urls.add(bid_url)
                    scraped_titles.add(bid_title)
                    extracted_count += 1
                    _dest = self._csv_filepath.name if self._csv_filepath else "database"
                    logger.info(f"[OK] {extracted_count} rows saved -> {_dest}")

            page += 1

        # All rows already written live via _append_row().
        # Log the final summary and return the absolute file path.
        abs_path = self._csv_filepath.resolve() if self._csv_filepath else None
        if abs_path:
            print(f"\n[SAM] Scraping complete - {extracted_count} rows saved to:\n"
                  f"      {abs_path}\n")
            logger.info(f"Scraping complete - {extracted_count} rows -> {abs_path}")
        self.close()
        return str(abs_path) if abs_path else None

    # ------------------------------------------------------------------
    # CSV save (fallback / full-save)
    # ------------------------------------------------------------------
    def save_csv(self) -> str | None:
        """Delegates to csv_handler.save_csv."""
        return _save_csv(self.data, self._csv_cfg, self._output_filename)

    def close(self):
        if self.driver:
            self.driver.quit()

    def get_screenshot_base64(self) -> str | None:
        """Capture the current browser state as a base64 string."""
        if not self.driver:
            return None
        try:
            return self.driver.get_screenshot_as_base64()
        except Exception:
            return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SAM.gov Scraper – date-filter only")
    parser.add_argument("--headless",    action="store_true", help="Run headless Chrome")
    parser.add_argument("--date-filter", default=None,        help="Start date YYYY-MM-DD")
    parser.add_argument("--max-records", type=int, default=None)
    args = parser.parse_args()

    scraper = SAMGovScraper(headless=args.headless, date_filter=args.date_filter)
    try:
        scraper.run(max_records=args.max_records)
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        # Rows were already written to disk live via _append_row(), so the
        # CSV contains everything scraped up to this point. No extra save needed.
        if scraper._csv_filepath:
            abs_path = scraper._csv_filepath.resolve()
            print(f"\n[SAM] [!] Stopped by user. Data saved to:\n      {abs_path}\n")
        scraper.close()
        scraper.close()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        scraper.save_csv()
        scraper.close()
