"""Shared Selenium machinery for all portal scrapers.

Subclass BaseScraper and implement the portal-specific flow. The base handles
the Chrome driver, a per-run staging download directory, download completion,
failure screenshots, and step/status reporting via run_manager.
"""

import logging
import shutil
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from app.config import settings
from app.core import run_manager
from app.core.filenames import sanitize_filename

logger = logging.getLogger(__name__)

WAIT_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 120


class BaseScraper:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.run_dir = run_manager.run_folder(run_id)
        # Staging dir for browser downloads; files are moved out after each finishes.
        self.download_dir = self.run_dir / "_downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.driver: webdriver.Chrome | None = None

    # -- lifecycle ----------------------------------------------------------

    def start_driver(self) -> None:
        options = Options()
        if settings.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_experimental_option(
            "prefs",
            {
                "download.default_directory": str(self.download_dir),
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
            },
        )
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)
        self.driver.set_page_load_timeout(60)

    def stop_driver(self) -> None:
        if self.driver:
            try:
                self.driver.quit()
            except WebDriverException:
                pass
            self.driver = None

    def cleanup(self) -> None:
        shutil.rmtree(self.download_dir, ignore_errors=True)
        self.stop_driver()

    # -- helpers ------------------------------------------------------------

    def wait(self, timeout: int = WAIT_TIMEOUT) -> WebDriverWait:
        return WebDriverWait(self.driver, timeout)

    def scroll_into_view(self, element) -> None:
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)

    def screenshot(self, name: str) -> None:
        if self.driver:
            try:
                self.driver.save_screenshot(str(self.run_dir / f"error_{sanitize_filename(name)}.png"))
            except WebDriverException:
                pass

    def set_step(self, step: str) -> None:
        logger.info("[run %s] %s", self.run_id, step)
        run_manager.update_run(self.run_id, step=step)

    def wait_for_download(self, timeout: int = DOWNLOAD_TIMEOUT) -> Path:
        """Wait for a new file to fully land in the staging download dir."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            in_progress = list(self.download_dir.glob("*.crdownload"))
            files = [f for f in self.download_dir.iterdir() if f.is_file() and f.suffix != ".crdownload"]
            if files and not in_progress:
                return max(files, key=lambda f: f.stat().st_mtime)
            time.sleep(0.5)
        raise TimeoutException(f"Download did not complete within {timeout}s")
