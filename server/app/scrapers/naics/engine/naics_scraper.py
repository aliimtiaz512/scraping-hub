"""
NAICS code scraper — fetches all 6-digit NAICS codes and industry titles
from a single page: https://www.naics.com/six-digit-naics/
Uses requests + BeautifulSoup (no browser / Selenium needed).
"""

import logging
import threading
from typing import Callable

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

PAGE_URL = "https://www.naics.com/six-digit-naics/"


class NaicsCodeScraper:
    def __init__(self):
        self._stop_event: threading.Event | None = None
        self._on_code_scraped: Callable[[dict], None] | None = None

        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        })

    def _check_stop(self):
        from .exceptions import check_stop
        check_stop(self._stop_event)

    def run(self) -> int:
        """
        Fetch the single NAICS index page and yield every 6-digit code.
        Calls self._on_code_scraped({code, title}) per entry found.
        Returns total number of codes yielded.
        """
        logger.info(f"Fetching NAICS index page: {PAGE_URL}")
        try:
            resp = self._session.get(PAGE_URL, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch NAICS page: {exc}") from exc

        soup = BeautifulSoup(resp.text, "html.parser")

        code_divs  = soup.find_all("div", class_="naicscode")
        title_divs = soup.find_all("div", class_="naicstit")
        logger.info(f"Found {len(code_divs)} naicscode divs on page")

        total = 0
        from .exceptions import StopScraping
        try:
            for code_div, title_div in zip(code_divs, title_divs):
                self._check_stop()

                code_text  = code_div.get_text(strip=True)
                title_text = title_div.get_text(strip=True)

                # Only keep 6-digit leaf codes (skip 2/4-digit parent codes and headers)
                if len(code_text) == 6 and code_text.isdigit():
                    item = {"code": code_text, "title": title_text}
                    if self._on_code_scraped:
                        self._on_code_scraped(item)
                    total += 1
        except StopScraping:
            logger.info("NAICS scraping interrupted by user")

        logger.info(f"NAICS scraper finished — {total} codes yielded")
        return total
