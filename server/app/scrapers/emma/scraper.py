"""Selenium automation for EMMA (eMaryland Marketplace Advantage).

EMMA is Maryland's procurement portal on the Ivalua platform — the same product
behind North Dakota's ND Buys (every control carries data-iv-* attributes).
Unlike ND, the login is a plain form directly on the page: Email/Username
(#body_x_txtLogin), Password (#body_x_txtPass) and a Log in submit button
(#body_x_btnLogin) — no OAuth/B2C redirect, so no CAPTCHA interception either.
The page encrypts the password into a hidden `crypted_pass` input on submit,
which is why the real Log in button must be clicked (never a bare form.submit).

Flow: sign in -> open the "Sourcing" nav dropdown and click "Public Solicitations"
(/page.aspx/en/rfp/request_browse_public) -> optionally apply the filter bar
(Main Category / Solicitation Type / Status) and Search -> page through the whole
results grid (#body_x_grid_grd), storing every row -> keep only solicitations
still >= 7 days from close (shared closing_filter) -> open each kept
solicitation's detail page and download its RFx Documents into a per-bid folder
-> persist to the DB -> build the per-run Excel from the DB and package the run
(Excel + documents) into one archive ZIP.
"""

import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC

from app.config import settings
from app.core import run_manager
from app.core.base_scraper import BaseScraper, StopRequested
from app.core.closing_filter import MIN_DAYS_UNTIL_CLOSE, days_until_close
from app.core.exports import archive_run
from app.core.filenames import sanitize_filename
from app.scrapers.emma import export
from app.services.notifier import notify_scrape_completion

logger = logging.getLogger(__name__)

LOGIN_URL = "https://emma.maryland.gov/page.aspx/en/usr/login"
BASE_URL = "https://emma.maryland.gov"
LOGIN_REDIRECT_WAIT = 30  # seconds to wait for the post-login redirect

# Ivalua login form controls, confirmed against the live page HTML.
USERNAME_ID = "body_x_txtLogin"   # placeholder "Email / Username"
PASSWORD_ID = "body_x_txtPass"
LOGIN_BTN_ID = "body_x_btnLogin"  # the "Log in" submit button

# The path token that stays in the URL while unauthenticated.
LOGIN_URL_MARKER = "/usr/login"

# Text that betrays a failed sign-in still sitting on the login page.
LOGIN_ERROR_XPATH = (
    "//*[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'invalid') "
    "or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'incorrect') "
    "or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'failed') "
    "or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'locked') "
    "or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'not recognized')]"
)

# -- post-login navigation ----------------------------------------------------
SOURCING_MENU_TEXT = "Sourcing"
PUBLIC_SOLICITATIONS_TEXT = "Public Solicitations"
PUBLIC_SOLICITATIONS_HREF = "request_browse_public"

# -- filter bar + results grid (Ivalua ids, confirmed live against the labelled
# controls: label "Keywords" -> txtBpmCodeCalculated_3, "Status" ->
# selStatusCode_search, "Category" -> selFamily_search. Note selStatusCode_2 is
# actually "Award Status", not the Status filter.) --------------------------
KEYWORDS_ID = "body_x_txtBpmCodeCalculated_3"        # "Keywords" free-text box
STATUS_SEARCH_ID = "body_x_selStatusCode_search"     # "Status" autocomplete (Open/Closed/Response Opened)
CATEGORY_SEARCH_ID = "body_x_selFamily_search"       # "Category" autocomplete
SEARCH_BTN_ID = "body_x_prxFilterBar_x_cmdSearchBtn"

GRID_ID = "body_x_grid_grd"
ROW_CSS = "#body_x_grid_grd tbody tr[data-id]"
NEXT_BTN_ID = "body_x_grid_gridPagerBtnNextPage"

# -- per-solicitation documents (on the detail page) --------------------------
# The detail page's "RFx Documents" grid loads lazily via an update panel; each
# document row carries a direct download anchor (/bare.aspx/en/fil/download/...)
# whose link text is the file name. Confirmed against a live detail page.
DOC_GRID_ID = "body_x_tabc_rfp_ext_prxrfp_ext_x_prxDoc_x_grid_grd"
DOC_ROW_CSS = f"#{DOC_GRID_ID} tbody tr[data-id]"
DOC_LINK_CSS = f"#{DOC_GRID_ID} a[href*='/fil/download/']"
DOC_GRID_WAIT = 20        # seconds to wait for the documents grid to populate
# Recycle the browser proactively every N solicitations during the document
# crawl, so a long headless session doesn't grow until Chrome is OOM-killed.
RECYCLE_EVERY = 75

MAX_PAGES = 200       # pagination safety guard
PREVIEW_LIMIT = 100   # rows mirrored to the live run state for the UI table

# Read every grid row via one JS pass. Column order confirmed from the live grid:
# td[0]=edit/detail link, [1]=ID, [2]=Title, [3]=Status, [4]=Due/Close Date,
# [5]=Publish Date, [6]=Main Category, [7]=Solicitation Type, [8]=Issuing Agency,
# [11]=Time Remaining, [12]=Award Status, [13]=Procurement Officer/Buyer.
_JS_SCRAPE = """
    const norm = (el) => ((el ? el.textContent : '') || '').replace(/\\s+/g, ' ').trim();
    const table = document.getElementById('body_x_grid_grd');
    if (!table) return [];
    const out = [];
    for (const tr of table.querySelectorAll('tbody > tr[data-id]')) {
      const tds = tr.querySelectorAll('td');
      if (tds.length < 9) continue;
      const link = tr.querySelector("a[href*='process_manage_extranet']");
      out.push({
        emma_id: tr.getAttribute('data-id'),
        detail_url: link ? link.getAttribute('href') : null,
        bpm_code: norm(tds[1]),
        title: norm(tds[2]),
        status: norm(tds[3]),
        close_date: norm(tds[4]),
        publish_date: norm(tds[5]),
        main_category: norm(tds[6]),
        solicitation_type: norm(tds[7]),
        issuing_agency: norm(tds[8]),
        time_remaining: tds.length > 11 ? norm(tds[11]) : '',
        award_status: tds.length > 12 ? norm(tds[12]) : '',
        procurement_officer: tds.length > 13 ? norm(tds[13]) : '',
      });
    }
    return out;
"""


class EmmaScraper(BaseScraper):
    def __init__(
        self,
        run_id: str,
        keyword: str = "",
        status: str = "",
        category: str = "",
    ):
        super().__init__(run_id)
        self.keyword = (keyword or "").strip()
        self.status = (status or "").strip()
        self.category = (category or "").strip()
        self.excel_path: Path | None = None
        # Full in-memory copy of every kept row — the Excel fallback source if the
        # DB is unavailable.
        self._records: list[dict[str, Any]] = []
        # Close-date filter tallies (see app/core/closing_filter).
        self._skipped_closing_soon = 0
        self._kept_unreadable_close = 0

    # -- helpers ------------------------------------------------------------

    def _abs_url(self, href: str | None) -> str | None:
        if not href:
            return None
        if href.startswith("http"):
            return href
        return BASE_URL + ("" if href.startswith("/") else "/") + href

    def _filters_summary(self) -> str:
        return ", ".join(
            part for part in (
                f"keyword={self.keyword}" if self.keyword else "",
                f"status={self.status}" if self.status else "",
                f"category={self.category}" if self.category else "",
            ) if part
        ) or "all public solicitations"

    def _has_filters(self) -> bool:
        return bool(self.keyword or self.status or self.category)

    def _safe_click(self, element) -> bool:
        """Click, falling back to a JS click if something overlays the target."""
        try:
            element.click()
            return True
        except WebDriverException:
            try:
                self.driver.execute_script("arguments[0].click();", element)
                return True
            except WebDriverException:
                return False

    def _login_error_text(self) -> str:
        for el in self.driver.find_elements(By.XPATH, LOGIN_ERROR_XPATH):
            try:
                text = (el.text or "").strip()
                if text:
                    return text[:200]
            except WebDriverException:
                continue
        return ""

    def _click_by_text(self, tags: list[str], text: str, timeout: int = 20) -> None:
        """Click the first *visible* element among `tags` whose text matches `text`.

        Ivalua menus render items as buttons/anchors/list items, ship a duplicate
        hidden responsive nav, and wire up their click handlers a beat after the
        element appears — so we poll for a *displayed* match over the whole window,
        scroll it into view, and try a native then a JS click.
        """
        conditions = " or ".join(f"self::{tag}" for tag in tags)
        xpath = (
            f"//*[({conditions})]"
            f"[contains(normalize-space(.), {_xpath_literal(text)})]"
        )
        deadline = time.monotonic() + timeout
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            try:
                elements = self.driver.find_elements(By.XPATH, xpath)
            except WebDriverException:
                elements = []
            for el in elements:
                try:
                    if not (el.is_displayed() and el.is_enabled()):
                        continue
                    self.scroll_into_view(el)
                    try:
                        el.click()
                    except WebDriverException:
                        self.driver.execute_script("arguments[0].click();", el)
                    return
                except WebDriverException as exc:
                    last_err = exc
                    continue
            time.sleep(0.5)

        self.screenshot(f"click_failed_{text}")
        self._dump_page(f"click_failed_{text}")
        raise TimeoutException(
            f"could not click a visible '{text}' element within {timeout}s"
        ) from last_err

    def _dump_page(self, name: str) -> None:
        """Save the current page HTML into the run folder for debugging."""
        try:
            path = self.run_dir / f"page_{sanitize_filename(name)}.html"
            path.write_text(self.driver.page_source, encoding="utf-8")
            logger.info("[run %s] saved page HTML -> %s", self.run_id, path)
        except Exception:  # noqa: BLE001 — diagnostics must never break the run
            pass

    # -- login --------------------------------------------------------------

    def login(self) -> None:
        self.set_step("logging_in")

        if not settings.emma_username or not settings.emma_password:
            raise WebDriverException(
                "EMMA credentials are empty — set EMMA_USERNAME and "
                "EMMA_PASSWORD in server/.env, then start the run again."
            )

        url = settings.emma_link or LOGIN_URL
        logger.info("[run %s] navigating to %s", self.run_id, url)
        # EMMA resolves via a CNAME into the .app TLD (maryland.ivalua.app), which
        # some ISP resolvers answer with an empty record set — so the first lookup
        # can fail even though the host is reachable. navigate() retries those.
        self.navigate(url)

        try:
            user_field = self.wait(LOGIN_REDIRECT_WAIT).until(
                EC.presence_of_element_located((By.ID, USERNAME_ID))
            )
        except TimeoutException:
            self.screenshot("login_no_username")
            raise WebDriverException(
                "EMMA login: the username field (#body_x_txtLogin) never appeared — "
                "the login page may have changed or failed to load."
            )
        try:
            pwd_field = self.driver.find_element(By.ID, PASSWORD_ID)
        except WebDriverException:
            self.screenshot("login_no_pwd")
            raise WebDriverException(
                "EMMA login: the password field (#body_x_txtPass) was not found."
            )

        user_field.clear()
        user_field.send_keys(settings.emma_username)
        pwd_field.clear()
        pwd_field.send_keys(settings.emma_password)

        # Clicking the real Log in button matters: the page's own submit handler
        # encrypts the password into the hidden crypted_pass input.
        try:
            button = self.wait(10).until(EC.element_to_be_clickable((By.ID, LOGIN_BTN_ID)))
            logger.info("[run %s] clicking the Log in button", self.run_id)
            self._safe_click(button)
        except TimeoutException:
            logger.info("[run %s] Log in button not clickable; submitting with Enter", self.run_id)
            pwd_field.send_keys(Keys.RETURN)

        try:
            self.wait(LOGIN_REDIRECT_WAIT).until(
                lambda d: LOGIN_URL_MARKER not in d.current_url.lower()
            )
        except TimeoutException:
            logger.warning("[run %s] no redirect away from the login page yet", self.run_id)

        if LOGIN_URL_MARKER in self.driver.current_url.lower():
            message = self._login_error_text()
            self.screenshot("login_failed")
            detail = f" Portal said: {message}" if message else ""
            raise WebDriverException(
                "EMMA login did not complete — still on the login page. "
                f"Check the credentials in server/.env.{detail}"
            )

        logger.info("[run %s] login successful; landed on %s", self.run_id, self.driver.current_url)

    # -- navigation ---------------------------------------------------------

    def open_public_solicitations(self) -> None:
        """Open the top-nav "Sourcing" dropdown and click "Public Solicitations"."""
        self.set_step("opening_sourcing_menu")
        self._click_by_text(["button"], SOURCING_MENU_TEXT, timeout=30)
        time.sleep(1)  # let the dropdown's open transition finish

        self.set_step("opening_public_solicitations")
        clicked = False
        for link in self.driver.find_elements(
            By.CSS_SELECTOR, f"a[href*='{PUBLIC_SOLICITATIONS_HREF}']"
        ):
            try:
                if link.is_displayed():
                    self.scroll_into_view(link)
                    if self._safe_click(link):
                        clicked = True
                        break
            except WebDriverException:
                continue
        if not clicked:
            self._click_by_text(["a", "button", "span", "li"], PUBLIC_SOLICITATIONS_TEXT, timeout=20)

        try:
            self.wait(30).until(lambda d: PUBLIC_SOLICITATIONS_HREF in d.current_url.lower())
        except TimeoutException:
            self.screenshot("public_solicitations_not_reached")
            self._dump_page("public_solicitations_not_reached")
            raise WebDriverException(
                "EMMA navigation did not reach the Public Solicitations page — "
                "the Sourcing menu or its Public Solicitations item may have changed."
            )
        try:
            self.wait(30).until(EC.presence_of_element_located((By.ID, GRID_ID)))
        except TimeoutException:
            logger.warning("[run %s] no #%s grid on the page yet", self.run_id, GRID_ID)

        logger.info("[run %s] Public Solicitations open at %s", self.run_id, self.driver.current_url)

    # -- filters + search ---------------------------------------------------

    def apply_filters(self) -> None:
        """Apply whichever filter-bar fields the user supplied, then Search.

        Keywords / Status / Category are optional and combinable — the same three
        fields the portal shows above the results. With none set, the grid already
        lists every public solicitation, so we skip straight to scraping. Each
        filter is best-effort: a failure warns and continues rather than aborting.
        """
        if not self._has_filters():
            logger.info("[run %s] no filters set — scraping the full list", self.run_id)
            return

        self.set_step("applying_filters")
        if self.keyword:
            self._fill_keywords(self.keyword)
        if self.status:
            self._select_autocomplete(STATUS_SEARCH_ID, self.status, "status")
        if self.category:
            self._select_autocomplete(CATEGORY_SEARCH_ID, self.category, "category")

        self.set_step("searching")
        first_before = self._first_row_id()
        try:
            self.wait(15).until(EC.element_to_be_clickable((By.ID, SEARCH_BTN_ID))).click()
        except WebDriverException:
            self.driver.execute_script(
                "var b = document.getElementById(arguments[0]); if (b) b.click();", SEARCH_BTN_ID
            )
        self._wait_grid_settled(first_before)

    def _fill_keywords(self, value: str) -> None:
        """Type into the plain "Keywords" text box (best-effort)."""
        try:
            box = self.wait(10).until(EC.presence_of_element_located((By.ID, KEYWORDS_ID)))
            self.scroll_into_view(box)
            box.clear()
            box.send_keys(value)
            logger.info("[run %s] applied keyword filter %r", self.run_id, value)
        except WebDriverException:
            logger.info("[run %s] keyword field not found for %r; continuing", self.run_id, value)
            run_manager.add_warning(self.run_id, f"could not enter keyword '{value}'")

    def _select_autocomplete(self, search_id: str, value: str, label: str) -> None:
        """Type into an Ivalua Semantic-UI autocomplete and pick the best match.

        Prefers an option whose text matches `value` (case-insensitive); falls back
        to the first suggestion. Non-fatal: a failure warns and the run continues.
        """
        try:
            box = self.wait(10).until(EC.presence_of_element_located((By.ID, search_id)))
            self.scroll_into_view(box)
            box.click()
            box.clear()
            box.send_keys(value)
            time.sleep(1.5)  # debounce + async option fetch
            options = self.wait(10).until(EC.presence_of_all_elements_located((
                By.CSS_SELECTOR,
                ".ui.dropdown .menu.visible .item, .ui.dropdown .menu .item, "
                ".results .result, .autocomplete-suggestions .autocomplete-suggestion",
            )))
            wanted = value.strip().lower()
            target = next(
                (o for o in options if o.is_displayed() and wanted in (o.text or "").strip().lower()),
                None,
            )
            if target is None:
                target = next((o for o in options if o.is_displayed()), None)
            if target is None:
                raise WebDriverException("no visible autocomplete option")
            self.scroll_into_view(target)
            self._safe_click(target)
            logger.info("[run %s] applied %s filter %r", self.run_id, label, value)
        except WebDriverException:
            logger.info("[run %s] %s autocomplete failed for %r; continuing", self.run_id, label, value)
            run_manager.add_warning(self.run_id, f"could not apply {label} filter '{value}'")

    # -- results ------------------------------------------------------------

    def _read_rows(self) -> list[dict[str, Any]]:
        try:
            data = self.driver.execute_script(_JS_SCRAPE)
        except WebDriverException:
            return []
        rows: list[dict[str, Any]] = []
        for row in data or []:
            if not row.get("emma_id"):
                continue
            row["detail_url"] = self._abs_url(row.get("detail_url"))
            row["matched_filters"] = self._filters_summary() if self._has_filters() else ""
            row["documents"] = []
            rows.append(row)
        return rows

    def _first_row_id(self) -> str | None:
        try:
            return self.driver.execute_script(
                "var tr = document.querySelector(\"#body_x_grid_grd tbody tr[data-id]\");"
                "return tr ? tr.getAttribute('data-id') : null;"
            )
        except WebDriverException:
            return None

    def _row_count(self) -> int:
        try:
            return int(self.driver.execute_script(
                "return document.querySelectorAll(\"#body_x_grid_grd tbody tr[data-id]\").length;"
            ) or 0)
        except WebDriverException:
            return 0

    def _wait_grid_settled(self, first_before: str | None, timeout: int = 40) -> int:
        """Wait for the grid to finish repainting after a search/page change."""
        deadline = time.monotonic() + timeout
        prev = -1
        stable = 0
        while time.monotonic() < deadline:
            try:
                self.wait(5).until(EC.presence_of_element_located((By.CSS_SELECTOR, ROW_CSS)))
            except TimeoutException:
                if time.monotonic() - (deadline - timeout) > 6:
                    return 0
                continue
            first_now = self._first_row_id()
            count = self._row_count()
            if first_before is not None and first_now != first_before:
                return count
            if count == prev:
                stable += 1
                if stable >= 2:
                    return count
            else:
                stable = 0
            prev = count
            time.sleep(0.5)
        return self._row_count()

    def _next_disabled(self) -> bool:
        try:
            return bool(self.driver.execute_script(
                "var b = document.getElementById(arguments[0]);"
                "return b ? (b.className.indexOf('disabled') !== -1 || b.disabled) : true;",
                NEXT_BTN_ID,
            ))
        except WebDriverException:
            return True

    def _go_next_page(self) -> bool:
        """Advance one page via the grid's Next button, confirmed by the first
        row's data-id turning over."""
        if self._next_disabled():
            return False
        before = self._first_row_id()
        try:
            btn = self.wait(10).until(EC.element_to_be_clickable((By.ID, NEXT_BTN_ID)))
            self.scroll_into_view(btn)
            btn.click()
        except WebDriverException:
            try:
                self.driver.execute_script(
                    "var b = document.getElementById(arguments[0]); if (b) b.click();", NEXT_BTN_ID
                )
            except WebDriverException:
                return False
        return self._wait_turnover(before, timeout=40)

    def _wait_turnover(self, before: str | None, timeout: int) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.6)
            now = self._first_row_id()
            if now and now != before:
                return True
        return False

    def scrape_all_pages(self) -> None:
        self.set_step("scraping_results")
        preview: list[dict[str, Any]] = []
        seen: set[str] = set()
        scraped = 0
        pages = 0

        while pages < MAX_PAGES:
            pages += 1
            for rec in self._read_rows():
                key = rec.get("emma_id")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)

                # Keep only solicitations still at least MIN_DAYS_UNTIL_CLOSE days
                # from their Due/Close Date; an unreadable close date is kept.
                days_left = days_until_close(rec.get("close_date"))
                if days_left is None:
                    self._kept_unreadable_close += 1
                elif days_left < MIN_DAYS_UNTIL_CLOSE:
                    self._skipped_closing_soon += 1
                    continue

                self._records.append(rec)
                scraped += 1
                if len(preview) < PREVIEW_LIMIT:
                    preview.append({**rec, "error": None})
            run_manager.update_run(
                self.run_id, bids_found=scraped, bids_processed=scraped, bids=list(preview)
            )
            logger.info("[run %s] page %s scraped (kept total %s)", self.run_id, pages, scraped)

            if not self._go_next_page():
                break

        if pages >= MAX_PAGES:
            run_manager.add_error(self.run_id, f"stopped at page cap ({MAX_PAGES})")

    # -- documents ----------------------------------------------------------

    def download_all_documents(self) -> None:
        """Open each kept solicitation's detail page and download its documents
        into a per-bid folder under the run folder. Updates each record's
        `documents` list and the run's documents_downloaded total.

        Resilient to the browser dying mid-crawl: this is a long session (one
        detail page per solicitation, hundreds of downloads), and headless Chrome
        can be OOM-killed or crash. Rather than spinning through every remaining
        solicitation logging instant "invalid session id" failures, a dead session
        is detected and the browser is restarted + re-logged-in once, then the
        same solicitation is retried; if the restart itself fails, the document
        phase stops cleanly and the run keeps everything gathered so far. The
        browser is also recycled proactively every RECYCLE_EVERY solicitations to
        keep the session from growing unbounded in the first place.
        """
        self.set_step("downloading_documents")
        total = 0
        since_recycle = 0
        for index, rec in enumerate(self._records, start=1):
            self.raise_if_stopped()  # a long doc crawl must respond to Stop
            url = rec.get("detail_url")
            if not url:
                continue

            # Proactively recycle the browser so a very long crawl doesn't grow
            # the session until Chrome is killed.
            if since_recycle >= RECYCLE_EVERY:
                logger.info("[run %s] recycling browser after %s solicitations", self.run_id, since_recycle)
                if self._restart_browser():
                    since_recycle = 0
                else:
                    run_manager.add_error(
                        self.run_id, "could not restart the browser during document download; "
                        "stored everything gathered so far",
                    )
                    break

            code = rec.get("bpm_code") or rec.get("emma_id") or f"bid{index}"
            self.set_step(f"downloading_documents:{code}")
            try:
                names = self._download_solicitation_docs(url, rec)
            except StopRequested:
                raise  # user Stop — let it unwind, never treat it as a doc failure
            except Exception as exc:  # noqa: BLE001 — classify before deciding
                if self._is_dead_session_error(exc):
                    # The browser process is gone. Restart + re-login once, then
                    # retry this solicitation; if recovery fails, stop the phase.
                    logger.warning("[run %s] browser session lost at %s — restarting", self.run_id, code)
                    if not self._restart_browser():
                        run_manager.add_error(
                            self.run_id, "browser session was lost during document download and "
                            "could not be restarted; stored everything gathered so far",
                        )
                        break
                    since_recycle = 0
                    try:
                        names = self._download_solicitation_docs(url, rec)
                    except StopRequested:
                        raise
                    except Exception as exc2:  # noqa: BLE001
                        if self._is_dead_session_error(exc2):
                            run_manager.add_error(
                                self.run_id, "browser session kept dropping during document "
                                "download; stored everything gathered so far",
                            )
                            break
                        rec.setdefault("document_errors", []).append(str(exc2)[:200])
                        names = []
                else:
                    logger.info("[run %s] docs failed for %s: %s", self.run_id, code, exc.__class__.__name__)
                    rec.setdefault("document_errors", []).append(str(exc)[:200])
                    names = []
            rec["documents"] = names
            total += len(names)
            since_recycle += 1
            run_manager.update_run(self.run_id, documents_downloaded=total)
            logger.info("[run %s] %s: %s document(s) (running total %s)",
                        self.run_id, code, len(names), total)
        logger.info("[run %s] downloaded %s documents across %s solicitations",
                    self.run_id, total, len(self._records))

    def _restart_browser(self) -> bool:
        """Tear down and relaunch the browser, then re-login. Returns True on
        success. Used to recover from (or pre-empt) a dead session mid-crawl."""
        try:
            self.stop_driver()
            time.sleep(1)
            self.start_driver()
            self.login()
            return True
        except Exception:  # noqa: BLE001 — a failed restart just ends the doc phase
            logger.exception("[run %s] browser restart failed", self.run_id)
            return False

    # Extract every labelled field on a solicitation's detail page. Different
    # solicitations expose different fields (pre-bid conference, MBE %, contact
    # email, alternate link, …), so this captures whatever is there rather than a
    # fixed list. Values are cleaned of Ivalua chrome: the clear-button ("Delete
    # the value"), the label echoed into the body, and encrypted tokens (CfDJ…).
    _JS_DETAIL_FIELDS = r"""
        const NOISE=/^(default|high contrast|yes|no|show|hide|show \/ hide column|actions?|select this row|edit)$/i;
        const CLEAR=/delete the value\.?/ig;
        const panel = document.getElementById('body_x_tabc_rfp_ext_prxrfp_ext_x')
                   || document.getElementById('body_x_tabc_rfp_ext')
                   || document.getElementById('pageContent') || document.body;
        const out={};
        panel.querySelectorAll("[data-iv-role='field']").forEach(f=>{
          // Skip anything inside the documents grid — those are handled separately.
          if (f.closest("[id*='prxDoc']")) return;
          let labEl=f.querySelector("label,.control-label,.field-label,.iv-label");
          let L=labEl?(labEl.textContent||'').replace(/\s+/g,' ').replace(/\*/g,'').trim():'';
          if(!L || NOISE.test(L) || L.length>60) return;
          let V='';
          let sel=f.querySelector("select");
          let inp=f.querySelector("input[type='text'],input:not([type]),textarea");
          if(sel && sel.selectedIndex>=0){ V=(sel.options[sel.selectedIndex].textContent||'').trim(); }
          else if(inp){ V=(inp.value||'').trim(); }
          else {
            let body=f.querySelector(".iv-field-body,.field-body,.iv-value,.value");
            V=((body||f).textContent||'');
            if(labEl) V=V.replace(labEl.textContent,'');
          }
          V=V.replace(CLEAR,'').replace(/\s+/g,' ').trim();
          if(!V || NOISE.test(V) || V.startsWith('CfDJ')) return;
          if(V.length>1000) V=V.slice(0,1000);
          if(!(L in out)) out[L]=V;
        });
        return out;
    """

    def _extract_detail_fields(self) -> dict[str, str]:
        """Return {label: value} for every labelled field on the current detail
        page. Never raises — a failure just yields an empty dict."""
        try:
            data = self.driver.execute_script(self._JS_DETAIL_FIELDS)
            return {str(k): str(v) for k, v in (data or {}).items() if k and v}
        except WebDriverException:
            return {}

    # Detail-page labels whose (cleaner) value should fill a grid column when the
    # grid left it blank or gave an encrypted token — e.g. the grid renders the
    # Procurement Officer as a CfDJ… token, but the detail page has the real name.
    _DETAIL_BACKFILL = {
        "procurement_officer": ("Procurement Officer / Buyer",),
        "close_date": ("Due / Close Date (EST)", "Due / Close Date"),
        "main_category": ("Main Category",),
        "issuing_agency": ("Issuing Agency",),
        "solicitation_type": ("Solicitation Type",),
        "status": ("Status",),
    }

    def _backfill_from_detail(self, rec: dict[str, Any], detail: dict[str, str]) -> None:
        """Fill a grid column from the detail page when the grid value is missing
        or an encrypted token."""
        for field, labels in self._DETAIL_BACKFILL.items():
            current = (rec.get(field) or "").strip()
            if current and not current.startswith("CfDJ"):
                continue
            for label in labels:
                val = (detail.get(label) or "").strip()
                if val and not val.startswith("CfDJ"):
                    rec[field] = val
                    break

    def _download_solicitation_docs(self, url: str, rec: dict[str, Any]) -> list[str]:
        """Open a solicitation's detail page: extract all of its fields into the
        record, then download every file in its RFx Documents grid.

        The grid loads lazily, so we scroll it into view and wait for its rows;
        each file's direct download anchor is clicked (Chrome saves it to the
        staging dir) and moved into this solicitation's folder. A solicitation
        with no documents returns an empty list.
        """
        self.navigate(url)
        time.sleep(2)  # let the detail form finish rendering its fields

        # Capture all of this solicitation's fields (varies per solicitation) so
        # the record — and the Excel/DB — carry the full detail, not just the grid
        # row. Stored under `detail`; also fills any grid column left blank.
        detail = self._extract_detail_fields()
        if detail:
            rec["detail"] = detail
            self._backfill_from_detail(rec, detail)

        links = self._collect_document_links()
        if not links:
            return []

        code = rec.get("bpm_code") or rec.get("emma_id") or "bid"
        title = rec.get("title") or ""
        bid_folder = self.run_dir / f"{code} - {_safe_title(title)}"
        bid_folder.mkdir(parents=True, exist_ok=True)

        saved: list[str] = []
        for filename, href in links:
            self.raise_if_stopped()
            try:
                self._clear_staging()
                # A JS anchor click forces the browser to fetch the file endpoint
                # as a download without navigating the current tab away.
                self.driver.execute_script(
                    "var a=document.createElement('a');a.href=arguments[0];a.download='';"
                    "document.body.appendChild(a);a.click();a.remove();",
                    href,
                )
                downloaded = self.wait_for_download()
                dest = self._unique_path(bid_folder / (filename or downloaded.name))
                shutil.move(str(downloaded), str(dest))
                saved.append(dest.name)
            except (TimeoutException, OSError, WebDriverException) as exc:
                logger.info("[run %s] could not download %r: %s", self.run_id, filename, exc.__class__.__name__)
                rec.setdefault("document_errors", []).append(f"{filename}: {exc.__class__.__name__}")
        return saved

    def _collect_document_links(self) -> list[tuple[str, str]]:
        """Wait for the documents grid to populate, then return (filename, href)
        for every download anchor. Empty when the solicitation has no documents."""
        deadline = time.monotonic() + DOC_GRID_WAIT
        while time.monotonic() < deadline:
            try:
                self.driver.execute_script(
                    "var e=document.getElementById(arguments[0]); if (e) e.scrollIntoView();", DOC_GRID_ID
                )
            except WebDriverException:
                pass
            anchors = self.driver.find_elements(By.CSS_SELECTOR, DOC_LINK_CSS)
            if anchors:
                links: list[tuple[str, str]] = []
                for a in anchors:
                    try:
                        href = a.get_attribute("href")
                        name = (a.text or "").strip() or (a.get_attribute("title") or "").strip()
                        if href:
                            links.append((name, href))
                    except WebDriverException:
                        continue
                return links
            # No anchors yet — is the grid present but empty (no documents)?
            try:
                if self.driver.find_elements(By.ID, DOC_GRID_ID) and not self.driver.find_elements(
                    By.CSS_SELECTOR, DOC_ROW_CSS
                ):
                    # Grid rendered with zero rows — genuinely no documents.
                    if time.monotonic() - (deadline - DOC_GRID_WAIT) > 5:
                        return []
            except WebDriverException:
                pass
            time.sleep(1)
        return []

    def _clear_staging(self) -> None:
        """Remove any leftover files in the browser download staging dir so
        wait_for_download only ever sees the file we just triggered."""
        for f in self.download_dir.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                except OSError:
                    pass

    def _unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem, suffix, counter = path.stem, path.suffix, 2
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
            self.open_public_solicitations()
            self.apply_filters()
            self.scrape_all_pages()

            # Surface the close-date filter's effect (see app/core/closing_filter).
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

            # Open each kept solicitation's detail page and download its documents
            # into a per-bid folder (populating each record's `documents` list
            # before it is persisted).
            self.download_all_documents()

            # Persist every kept solicitation in one transaction (mirrors North
            # Dakota). Best-effort: a DB failure falls back to an Excel-from-records.
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
                # The sheet is rebuilt from the DB on demand (Download / email).
                run_manager.update_run(self.run_id, excel_exported=True)
            else:
                name = sanitize_filename(f"Emma_({self._filters_summary()})", max_length=150)
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

            # Package the run into one archive ZIP and delete the workspace.
            self.set_step("packaging_results")
            archive_run(self.run_id)

            run_manager.update_run(self.run_id, status="completed", step="done")
            notify_scrape_completion(self.run_id, "emma", len(self._records))
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

    def _save_run_row(self) -> None:
        run = run_manager.get_run(self.run_id)
        if not run:
            return
        try:
            export.save_run(run)
        except Exception:  # noqa: BLE001
            logger.exception("[run %s] save_run failed", self.run_id)


def _safe_title(title: str, max_length: int = 80) -> str:
    """A filesystem-safe fragment of a solicitation title for a folder name."""
    cleaned = "".join(c for c in (title or "") if c.isalnum() or c in " -_").strip()
    return (cleaned[:max_length].strip() or "untitled")


def _xpath_literal(text: str) -> str:
    """Quote a string for use inside an XPath expression."""
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    parts = text.split("'")
    return "concat('" + "', \"'\", '".join(parts) + "')"


def execute_run(
    run_id: str,
    keyword: str = "",
    status: str = "",
    category: str = "",
) -> None:
    EmmaScraper(run_id, keyword, status, category).run()
