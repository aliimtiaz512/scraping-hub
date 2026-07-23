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

from app.core import live, run_manager
from app.core.filenames import sanitize_filename

logger = logging.getLogger(__name__)

WAIT_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 120

# A realistic desktop-Chrome UA. Under --headless=new the default UA no longer
# leaks a "HeadlessChrome" token, but some portals (e.g. BidNet Direct) still
# 403 the automation fingerprint, so we pin a normal UA to match.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


class BaseScraper:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.run_dir = run_manager.run_folder(run_id)
        # Staging dir for browser downloads; files are moved out after each finishes.
        self.download_dir = self.run_dir / "_downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.driver: webdriver.Chrome | None = None
        # Last step reported via set_step — used to say where a failure happened.
        self.current_step: str | None = None

    # -- lifecycle ----------------------------------------------------------

    def start_driver(self, headless: bool | None = None, user_data_dir: str | None = None) -> None:
        """Launch Chrome. When `headless` is omitted, the run's own `live_preview`
        flag decides visibility: a run started from the "Live preview" button
        shows the browser, every other run is headless. An explicit `headless`
        argument overrides that (a portal that needs a human to solve a challenge
        forces it False). `user_data_dir` points Chrome at a persistent profile so
        cookies/session survive between runs."""
        options = Options()
        if headless is None:
            # Default: hidden, unless this run was launched as a live preview.
            run = run_manager.get_run(self.run_id) or {}
            headless = not run.get("live_preview", False)
        use_headless = headless
        if use_headless:
            options.add_argument("--headless=new")
        if user_data_dir:
            options.add_argument(f"--user-data-dir={user_data_dir}")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        # Return from driver.get() at DOMContentLoaded instead of the load event.
        # These portals pull third-party subresources (fonts, analytics) that can
        # stall for a minute on a flaky network, and waiting for the load event
        # times the renderer out even though the page itself is ready and usable.
        # Every flow here waits for the elements it needs anyway.
        options.page_load_strategy = "eager"
        # Strip the automation fingerprint that makes bot-protected portals return
        # 403 Forbidden: drop the "controlled by automated software" switches and
        # the AutomationControlled blink feature (which sets navigator.webdriver).
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(f"--user-agent={USER_AGENT}")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_experimental_option(
            "prefs",
            {
                "download.default_directory": str(self.download_dir),
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
                # A bid detail page downloads several attachments in a row; without
                # this Chrome silently blocks every download after the first.
                "profile.default_content_setting_values.automatic_downloads": 1,
            },
        )
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)
        self.driver.set_page_load_timeout(60)
        # Expose this run's browser to the shared live-screenshot endpoint so the
        # Live Preview modal can stream frames while it is open.
        live.register(self.run_id, self)
        # Belt-and-suspenders: ensure navigator.webdriver is undefined on every
        # document before the page's own scripts run, so bot checks don't see it.
        try:
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"},
            )
        except WebDriverException:
            pass

    def stop_driver(self) -> None:
        if self.driver:
            try:
                self.driver.quit()
            except WebDriverException:
                pass
            self.driver = None

    def cleanup(self) -> None:
        live.unregister(self.run_id)
        shutil.rmtree(self.download_dir, ignore_errors=True)
        self.stop_driver()

    def get_screenshot_base64(self) -> str | None:
        """A base64 PNG of the current browser view, or None. Used by the shared
        live-screenshot endpoint; defensive so a frame grab never breaks a run."""
        if not self.driver:
            return None
        try:
            return self.driver.get_screenshot_as_base64()
        except WebDriverException:
            return None

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
        self.current_step = step
        run_manager.update_run(self.run_id, step=step)

    def wait_for_download(self, timeout: int = DOWNLOAD_TIMEOUT) -> Path:
        """Wait for a new file to fully land in the staging download dir.

        Chrome marks an in-progress download with a `.crdownload` suffix or, on
        Linux, a hidden `.com.google.Chrome.XXXXXX` temp name — a file is only
        finished once neither pattern is present.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            partial = [
                f for f in self.download_dir.iterdir()
                if f.is_file() and (f.suffix == ".crdownload" or f.name.startswith(".com.google.Chrome."))
            ]
            done = [
                f for f in self.download_dir.iterdir()
                if f.is_file() and f.suffix != ".crdownload" and not f.name.startswith(".com.google.Chrome.")
            ]
            if done and not partial:
                return max(done, key=lambda f: f.stat().st_mtime)
            time.sleep(0.5)
        raise TimeoutException(f"Download did not complete within {timeout}s")
