"""Harvests the complete sidebar filter option lists from BidNet Direct.

The search sidebar renders only the ~12 highest-count options per panel inline;
everything else lives behind that panel's "View All" lightbox. `filters.py`
therefore ships a seeded catalog (what the sidebar renders inline, plus the fully
derived Location list) and this module is what replaces it with the portal's real,
complete lists.

It is a short scrape in its own right: reuse BidnetScraper's login untouched,
land on a search results page so the sidebar exists, walk every panel's "View All"
dialog, and write the harvest to the cache `filters.load_options` reads. Runs on
demand from the frontend's "Refresh options" button — the catalog is stable enough
that it does not belong in the scrape path.
"""

import logging
from datetime import datetime

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

from app.core import run_manager
from app.scrapers.bidnet import filters
from app.scrapers.bidnet.scraper import BASE_URL, BidnetScraper
from app.scrapers.bidnet.sidebar import SidebarDriver

logger = logging.getLogger(__name__)

# The results page the sidebar lives on. Reached directly when possible; a broad
# keyword search is the fallback for a portal that redirects a bare visit.
SEARCH_URL = f"{BASE_URL}/private/supplier/solicitations/search"

# Any term broad enough to return results (and therefore a populated sidebar) if
# the direct URL does not land on the search page.
FALLBACK_SEARCH_TERM = "services"


class FilterOptionDiscovery(BidnetScraper):
    """A BidNet run that scrapes filter *options* instead of solicitations.

    Subclasses the scraper purely to inherit its login and search verbatim —
    nothing in the login or keyword-search flow is changed or re-implemented here.
    """

    def __init__(self, run_id: str):
        super().__init__(run_id, keywords=[])

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        try:
            self.start_driver()
            self.login()
            self._open_search_page()

            self.set_step("harvesting_filter_options")
            harvested = SidebarDriver(
                self.driver,
                note=lambda message: run_manager.add_error(self.run_id, message),
            ).harvest()

            discovered_at = datetime.now().isoformat(timespec="seconds")
            filters.save_discovered(harvested, discovered_at)
            counts = {name: len(values) for name, values in harvested.items()}
            logger.info("[run %s] filter options discovered: %s", self.run_id, counts)
            run_manager.update_run(
                self.run_id,
                status="completed",
                step="done",
                filter_option_counts=counts,
                discovered_at=discovered_at,
            )
        except Exception as exc:  # noqa: BLE001 — a failed pass is reported, never crashes the worker
            logger.exception("[run %s] filter option discovery failed", self.run_id)
            self.screenshot("filter_discovery")
            run_manager.add_error(self.run_id, self.describe_failure(exc))
            run_manager.update_run(self.run_id, status="failed", step="failed")
        finally:
            self.cleanup()
            run_manager.update_run(self.run_id, finished_at=datetime.now().isoformat())
            run_manager.remove_empty_folder(self.run_id)

    def _open_search_page(self) -> None:
        """Land on a results page, which is the only place the sidebar renders."""
        self.set_step("opening_search")
        self.driver.get(SEARCH_URL)
        try:
            self.wait(15).until(EC.presence_of_element_located((By.ID, "searchFilterDiv")))
            return
        except (TimeoutException, WebDriverException):
            logger.info("[run %s] direct search URL had no sidebar; searching instead", self.run_id)

        # Fall back to the ordinary keyword search — unchanged, inherited code.
        self.search(FALLBACK_SEARCH_TERM)
        self.wait(15).until(EC.presence_of_element_located((By.ID, "searchFilterDiv")))


def execute_discovery(run_id: str) -> None:
    FilterOptionDiscovery(run_id).run()
