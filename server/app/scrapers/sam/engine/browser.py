"""
SAM.gov Scraper — browser setup & timing helpers.

Handles Chrome driver initialization, page-load waits, Angular rendering
waits, and random delay between requests.
"""

import os
import time
import random
import logging

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

logger = logging.getLogger(__name__)


def setup_driver(headless: bool, cfg: dict) -> webdriver.Chrome:
    """
    Create and return a configured Chrome WebDriver instance.

    Parameters
    ----------
    headless : bool
        Run Chrome in headless mode.
    cfg : dict
        The ``sam`` config section (needs ``browser.user_agent``).
    """
    chrome_options = Options()
    if headless:
        # --headless=new is required for CDP Page.setDownloadBehavior to work
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")

    chrome_options.add_argument("--no-sandbox")

    # Suppress Chrome's download prompt so file-link clicks save automatically
    chrome_options.add_experimental_option("prefs", {
        "download.prompt_for_download": False,
        "download.directory_upgrade":   True,
        "safebrowsing.enabled":         True,
    })
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    chrome_options.add_argument(
        f"user-agent={cfg.get('browser', {}).get('user_agent', '')}"
    )
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--ignore-ssl-errors")
    chrome_options.add_argument("--log-level=3")

    try:
        driver_path = ChromeDriverManager().install()
        if not driver_path.endswith(".exe"):
            driver_dir = os.path.dirname(driver_path)
            found = False
            for root, _dirs, files in os.walk(driver_dir):
                if "chromedriver.exe" in files:
                    driver_path = os.path.join(root, "chromedriver.exe")
                    found = True
                    break
            if not found:
                parent = os.path.dirname(driver_dir)
                for root, _dirs, files in os.walk(parent):
                    if "chromedriver.exe" in files:
                        driver_path = os.path.join(root, "chromedriver.exe")
                        break
        service = Service(driver_path)
    except Exception as e:
        logger.warning(f"ChromeDriverManager failed: {e}. Falling back to PATH.")
        service = Service()

    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.maximize_window()
    logger.info("Chrome driver initialised successfully")
    return driver


def wait_for_page_load(driver, timeouts: dict) -> None:
    """Wait for document.readyState == 'complete'."""
    try:
        WebDriverWait(
            driver, timeouts.get("page_load_wait", 20)
        ).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        time.sleep(timeouts.get("page_load_sleep", 2))
    except Exception:
        pass


def wait_for_angular(driver) -> None:
    """
    Extra wait for Angular to finish rendering detail-page content.
    Waits up to 15 s for at least one field-like element to appear.
    """
    try:
        WebDriverWait(driver, 15).until(
            lambda d: d.find_elements(
                By.CSS_SELECTOR,
                "h1, [id*='notice'], [id*='department'], [id*='agency']"
            )
        )
    except Exception:
        pass
    time.sleep(1.5)


def random_delay(timeouts: dict) -> None:
    """Sleep for a random duration between delay_min and delay_max."""
    lo = timeouts.get("delay_min", 2)
    hi = timeouts.get("delay_max", 4)
    time.sleep(random.uniform(lo, hi))
