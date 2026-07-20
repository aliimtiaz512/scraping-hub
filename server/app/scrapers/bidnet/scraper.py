"""Selenium automation for the BidNet Direct vendor portal.

This is a faithful Selenium port of the original Playwright scraper
(backend/scraper.py): same flow, same selectors, same per-bid folder layout.

Flow: login -> keyword search -> "Member Agency Bids" filter -> paginate the
results table collecting solicitation links -> for each solicitation open its
detail page, scrape the fields, and download every document into a per-bid
folder -> persist to the DB -> generate a per-run Excel at the documents root.
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

from app.config import settings
from app.core import run_manager
from app.core.base_scraper import BaseScraper
from app.core.filenames import timestamp
from app.scrapers.bidnet import export

logger = logging.getLogger(__name__)

BASE_URL = "https://www.bidnetdirect.com"
DOC_DOWNLOAD_TIMEOUT = 30  # per-document; a missing download falls back to a direct fetch
MAX_PAGES = 100  # pagination safety guard, same as the original

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
    def __init__(self, run_id: str, keywords: list[str]):
        super().__init__(run_id)
        self.keywords = keywords
        # A combined run spans many keywords, so everything (documents + Excel)
        # lives in the single per-run folder the router already created
        # (e.g. <documents>/Document_Bids_BidnetDirect (<label>)), with a per-bid
        # subfolder inside it.
        run = run_manager.get_run(run_id) or {}
        folder = run.get("folder") or str(settings.documents_root / "Document_Bids_BidnetDirect")
        self.document_folder = Path(folder)
        self.document_folder.mkdir(parents=True, exist_ok=True)
        self.excel_path: Path | None = None

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
        self.wait().until(EC.element_to_be_clickable((By.ID, "header_btnLogin"))).click()

        self.wait().until(EC.presence_of_element_located((By.ID, "j_username")))
        self.driver.find_element(By.ID, "j_username").send_keys(settings.bidnet_username)
        self.driver.find_element(By.ID, "j_password").send_keys(settings.bidnet_password)
        self.driver.find_element(By.ID, "loginButton").click()

        try:
            self.wait().until(EC.presence_of_element_located((By.ID, "btnSolicitations")))
        except TimeoutException:
            self.screenshot("login_error")
            raise

    def search(self, keyword: str) -> None:
        self.set_step(f"searching: {keyword}")
        box = self.wait().until(EC.presence_of_element_located((By.ID, "solicitationSingleBoxSearch")))
        box.clear()
        box.send_keys(keyword)
        self.driver.find_element(By.ID, "topSearchButton").click()
        time.sleep(3)
        self.wait().until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".searchContentGroupContainer")))

    def filter_member_agency(self) -> None:
        self.set_step("filtering_member_agency")
        self.driver.find_element(By.CSS_SELECTOR, "div[search-content-group-id='2085061601']").click()
        time.sleep(4)
        self.wait().until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr.mets-table-row")))

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

            time.sleep(3)
            try:
                self.wait(10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr.mets-table-row")))
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

    def process_bid(self, link: str) -> dict[str, Any]:
        """Open one solicitation, scrape its fields, download its documents."""
        self.set_step("opening_bid")
        self.driver.get(link)
        try:
            self.wait(15).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".mets-field")))
        except TimeoutException:
            pass

        record: dict[str, Any] = {key: self._extract_field(label).strip() for key, label in DETAIL_FIELDS.items()}
        reference_number = record.get("reference_number") or ""
        title = record.get("title") or ""

        documents_count = self._document_count()
        record["documents_count"] = documents_count

        downloaded: list[str] = []
        if documents_count != "0":
            downloaded = self._download_documents(reference_number, title)
        record["documents"] = downloaded
        return record

    def _document_count(self) -> str:
        try:
            tab = self.wait(15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#docs-itemsAbstractTab a"))
            )
            count = tab.find_element(By.CSS_SELECTOR, ".tabCount").text
            return count.strip() or "0"
        except WebDriverException as exc:
            logger.info("[run %s] doc count unavailable: %s", self.run_id, exc.__class__.__name__)
            return "0"

    def _download_documents(self, reference_number: str, title: str) -> list[str]:
        saved: list[str] = []
        try:
            self.driver.find_element(By.CSS_SELECTOR, "#docs-itemsAbstractTab a").click()
            time.sleep(4)
        except WebDriverException as exc:
            logger.info("[run %s] could not open docs tab for %s: %s", self.run_id, reference_number, exc.__class__.__name__)
            return saved

        bid_folder = self.document_folder / f"{reference_number} - {_safe_title(title)}"
        bid_folder.mkdir(parents=True, exist_ok=True)

        buttons = self._download_buttons()
        logger.info("[run %s] %s download buttons for %s", self.run_id, len(buttons), reference_number)
        for index, button in enumerate(buttons):
            name = self._download_one(button, bid_folder, index)
            if name:
                saved.append(name)
        return saved

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

            # Search each keyword separately (never concatenated — one keyword per
            # query gives the best results) and collect its solicitation links,
            # deduplicating by link across keywords. Each link remembers every
            # keyword that surfaced it.
            link_keywords: dict[str, list[str]] = {}
            for keyword in self.keywords:
                try:
                    self.search(keyword)
                    self.filter_member_agency()
                    links = self.collect_links()
                except (TimeoutException, WebDriverException) as exc:
                    run_manager.add_error(self.run_id, f"search failed for '{keyword}': {exc.__class__.__name__}")
                    self.screenshot(f"search_{keyword}")
                    continue
                for link in links:
                    matched = link_keywords.setdefault(link, [])
                    if keyword not in matched:
                        matched.append(keyword)
                logger.info("[run %s] '%s' -> %s links (unique total %s)", self.run_id, keyword, len(links), len(link_keywords))

            run_manager.update_run(self.run_id, bids_found=len(link_keywords))
            logger.info("[run %s] %s unique solicitations across %s keyword(s)", self.run_id, len(link_keywords), len(self.keywords))

            # Process each unique solicitation once, recording all matching keywords.
            records: list[dict] = []
            for index, (link, matched) in enumerate(link_keywords.items()):
                record = {"reference_number": None, "title": None, "documents": [], "error": None}
                try:
                    record = self.process_bid(link)
                except (TimeoutException, WebDriverException) as exc:
                    record["error"] = str(exc)[:300]
                    run_manager.add_error(self.run_id, f"bid failed: {exc.__class__.__name__}")
                    self.screenshot(f"bid_{index}")
                record["matched_keyword"] = ", ".join(matched)
                run_manager.add_bid_result(self.run_id, record)
                records.append(record)

            # Persist every scraped solicitation in one transaction (mirrors
            # MyFlorida). Best-effort: a DB failure must not fail the run — the
            # Excel is then written straight from the in-memory records.
            run = run_manager.get_run(self.run_id) or {"run_id": self.run_id}
            db_ok = True
            try:
                stored = export.save_bids(run, records)
                run_manager.update_run(self.run_id, bids_stored_in_db=stored)
            except Exception:  # noqa: BLE001 — DB issues shouldn't abort the run
                db_ok = False
                logger.exception("[run %s] DB save failed", self.run_id)
                run_manager.add_error(self.run_id, "db save failed (see logs)")

            self.set_step("generating_excel")
            label = (run_manager.get_run(self.run_id) or {}).get("label") or timestamp()
            # Keep the run's Excel alongside its downloaded documents, inside the
            # per-keyword folder (e.g. Document_Bids_BidnetDirect_AI), not the root.
            self.excel_path = self.document_folder / f"Document_Bids_BidnetDirect ({label}).xlsx"
            try:
                if db_ok:
                    export.generate_excel(self.run_id, self.excel_path)
                else:
                    export.generate_excel_from_records(records, self.excel_path)
                run_manager.update_run(self.run_id, excel_path=str(self.excel_path), excel_exported=True)
            except Exception:  # noqa: BLE001 — never fail the run over the Excel
                logger.exception("[run %s] Excel generation failed", self.run_id)
                run_manager.add_error(self.run_id, "excel generation failed (see logs)")

            run_manager.update_run(self.run_id, status="completed", step="done")
        except Exception as exc:  # noqa: BLE001 — a failed run must be reported, not crash the worker
            logger.exception("[run %s] failed", self.run_id)
            self.screenshot("fatal")
            run_manager.add_error(self.run_id, str(exc)[:500])
            run_manager.update_run(self.run_id, status="failed", step="failed")
        finally:
            self.cleanup()
            run_manager.update_run(self.run_id, finished_at=datetime.now().isoformat())
            self._save_run_row()

    def _save_run_row(self) -> None:
        run = run_manager.get_run(self.run_id)
        if not run:
            return
        try:
            export.save_run(run)
        except Exception:  # noqa: BLE001
            logger.exception("[run %s] save_run failed", self.run_id)


def execute_run(run_id: str, keywords: list[str]) -> None:
    BidnetScraper(run_id, keywords).run()
