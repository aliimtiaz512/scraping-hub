"""Selenium automation for PPRA active tenders."""

import logging
import random
import time
from typing import Any
from urllib.parse import urlencode, urljoin

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select

from app.config import settings
from app.core import run_manager
from app.core.base_scraper import BaseScraper, StopRequested
from app.core.closing_filter import MIN_DAYS_UNTIL_CLOSE, days_until_close
from app.core.exports import archive_run
from app.scrapers.ppra import export, repository
from app.scrapers.ppra.parsers import parse_listing, parse_options, parse_total
from app.scrapers.ppra.schemas import PpraFilters, PpraOptions
from app.services.notifier import notify_scrape_completion

logger = logging.getLogger(__name__)

SEL = {
    "keyword": (By.NAME, "keyword"),
    "tender_number": (By.NAME, "tender_no"),
    "closing_date": (By.NAME, "closing_date"),
    "tender_type": (By.NAME, "tender_type"),
    "category": (By.NAME, "category"),
    "sector": (By.NAME, "sector"),
    "nature": (By.NAME, "nature"),
    "organization": (By.NAME, "organization"),
    "country": (By.NAME, "country"),
    "advertise_from": (By.NAME, "advertise_from"),
    "advertise_to": (By.NAME, "advertise_to"),
    "status": (By.NAME, "status"),
    "city": (By.NAME, "city"),
    "submit_btn": (By.CSS_SELECTOR, "button[type='submit'], input[type='submit']"),
    "table": (By.CSS_SELECTOR, "table"),
    "pagination": (By.CSS_SELECTOR, ".pagination, ul.pagination"),
}


class PpraScraper(BaseScraper):
    def __init__(
        self,
        run_id: str,
        filters: PpraFilters,
        options: PpraOptions,
        parent_run_id: str | None = None,
    ):
        super().__init__(run_id)
        self.filters = filters
        self.options = options
        self.parent_run_id = parent_run_id
        self._skipped_closing = 0
        self._kept_unverified = 0
        self.rows_scraped_total = 0

    def polite_delay(self) -> None:
        delay_min = (getattr(self.options, "delay_ms", None) or settings.ppra_delay_min_ms) / 1000.0
        delay_max = settings.ppra_delay_max_ms / 1000.0
        delay = random.uniform(delay_min, max(delay_min, delay_max))
        time.sleep(delay)

    def build_filtered_url(self, base_url: str, page: int = 1) -> str:
        """Construct the exact GET query URL matching the PPRA filter form."""
        f = self.filters
        params: dict[str, str] = {}
        if page > 1:
            params["page"] = str(page)

        if f.keyword:
            params["keyword"] = f.keyword.strip()
        if f.tender_number:
            params["tender_no"] = f.tender_number.strip()
        if f.closing_date:
            params["closing_date"] = f.closing_date.strip()
        if f.tender_type and f.tender_type != "ALL":
            # Map human readable to PPRA numeric IDs if given, or pass raw
            type_map = {
                "Tender Notice": "1",
                "Pre-qualification Notice": "2",
                "Pre-qualification Notice (PQ)": "2",
                "Request for Proposal": "3",
                "Request for Proposal (RFP)": "3",
                "Expression of Interest": "4",
                "Expression of Interest (EOI)": "4",
            }
            params["tender_type"] = type_map.get(f.tender_type, f.tender_type)
        if f.category and f.category != "ALL":
            cat_map = {
                "Goods": "1",
                "Works": "2",
                "Consultancy Services": "3",
                "Non-consultancy Services": "4",
            }
            params["procurement_category"] = cat_map.get(f.category, f.category)
        if f.sector and f.sector != "ALL":
            params["sector"] = f.sector
        if f.nature and f.nature != "ALL":
            nat_map = {"Local": "0", "International": "1"}
            params["tender_nature"] = nat_map.get(f.nature, f.nature)
        if f.organization:
            params["organization"] = f.organization.strip()
        if f.country:
            params["country"] = f.country.strip()
        if f.advertise_from:
            params["advertise_date_from"] = f.advertise_from.strip()
        if f.advertise_to:
            params["advertise_date_to"] = f.advertise_to.strip()
        if f.status and f.status != "ALL":
            stat_map = {"Published": "1", "Cancelled": "2"}
            params["status"] = stat_map.get(f.status, f.status)
        if f.city:
            params["city"] = f.city.strip()

        qs = urlencode(params)
        return f"{base_url}?{qs}" if qs else base_url

    def run(self) -> None:
        self.start_driver()
        run_manager.update_run(self.run_id, status="running", step="opening_site")
        self.raise_if_stopped()

        base_url = settings.ppra_base_url
        start_url = urljoin(base_url, settings.ppra_active_tenders_path)

        first_url = self.build_filtered_url(start_url, page=1)
        logger.info("[run %s] navigating to PPRA with filters: %s", self.run_id, first_url)
        self.navigate(first_url)
        self.raise_if_stopped()

        # Parse total count
        total_records, total_pages = parse_total(self.driver.page_source)
        logger.info("[run %s] parsed PPRA totals: %d tenders across %d pages", self.run_id, total_records, total_pages)

        run_manager.update_run(
            self.run_id,
            total_expected=total_records,
            total_pages=total_pages,
            bids_found=total_records,
            step="scraping_pages",
        )
        repository.update_run_progress(
            self.run_id, total_expected=total_records, total_pages=total_pages
        )

        parent_hashes = None
        if self.parent_run_id:
            parent_hashes = repository.get_parent_run_hashes(self.parent_run_id)

        current_page = 1
        rows_scraped_total = 0
        rows_new_total = 0
        rows_changed_total = 0

        while current_page <= total_pages:
            self.raise_if_stopped()
            run_manager.update_run(
                self.run_id,
                current_page=current_page,
                step=f"page_{current_page}_of_{total_pages}",
            )

            # Parse listings on current page
            page_records = parse_listing(self.driver.page_source, base_url=base_url)

            # Apply close date filter if requested
            filtered_records = []
            for rec in page_records:
                if self.options.apply_closing_filter:
                    days = days_until_close(rec.get("closing_at"))
                    if days is None:
                        self._kept_unverified += 1
                        filtered_records.append(rec)
                    elif days >= MIN_DAYS_UNTIL_CLOSE:
                        filtered_records.append(rec)
                    else:
                        self._skipped_closing += 1
                else:
                    filtered_records.append(rec)

            # Store in DB
            new_c, changed_c, unchanged_c = repository.upsert_bids(
                self.run_id, current_page, filtered_records, parent_hashes
            )
            repository.append_to_mirror(self.run_id, filtered_records)

            rows_scraped_total += len(filtered_records)
            self.rows_scraped_total = rows_scraped_total
            rows_new_total += new_c
            rows_changed_total += changed_c

            run_manager.update_run(
                self.run_id,
                bids_processed=rows_scraped_total,
                rows_new=rows_new_total,
                rows_changed=rows_changed_total,
                skipped_closing=self._skipped_closing,
                kept_unverified=self._kept_unverified,
                checkpoint={"page": current_page, "last_tender": filtered_records[-1]["tender_no"] if filtered_records else None},
            )
            repository.update_run_progress(
                self.run_id,
                current_page=current_page,
                rows_scraped=rows_scraped_total,
                rows_new=rows_new_total,
                rows_changed=rows_changed_total,
                skipped_closing=self._skipped_closing,
                kept_unverified=self._kept_unverified,
            )

            current_page += 1
            if current_page <= total_pages:
                self.polite_delay()
                # Navigate to next page with active filters preserved
                next_url = self.build_filtered_url(start_url, page=current_page)
                try:
                    self.navigate(next_url)
                except Exception as exc:
                    logger.warning("[run %s] navigation to page %d failed: %s", self.run_id, current_page, exc)
                    break

        run_manager.update_run(self.run_id, step="packaging_results")


def execute_run(
    run_id: str,
    filters_dict: dict[str, Any],
    options_dict: dict[str, Any],
    parent_run_id: str | None = None,
) -> None:
    """Worker task executed on the thread pool."""
    filters = PpraFilters(**filters_dict)
    options = PpraOptions(**options_dict)
    scraper = PpraScraper(run_id, filters, options, parent_run_id)

    try:
        scraper.run()
        archive_run(run_id)
        notify_scrape_completion(run_id, "ppra", scraper.rows_scraped_total)
        run_manager.update_run(run_id, status="completed", step="completed")
    except StopRequested:
        logger.info("[run %s] stopped gracefully by user", run_id)
        archive_run(run_id)
        run_manager.update_run(run_id, status="stopped", step="stopped")
    except Exception as exc:
        logger.exception("[run %s] PPRA run failed: %s", run_id, exc)
        run_manager.add_error(run_id, scraper.describe_failure(exc))
        run_manager.update_run(run_id, status="failed", step="failed")
    finally:
        scraper.cleanup()

