"""
SAM.gov Scraper — attachment downloader.

Downloads all public attachments from a SAM.gov opportunity detail page
into  temp_docs/<notice_id>/  using Selenium clicks + Chrome CDP.

Key design decisions
--------------------
* The attachment links (<a class="file-link">) carry NO href — they are
  Angular-rendered elements whose click handlers trigger a JS-initiated
  download.  We therefore cannot extract a URL and use requests; we must
  let Chrome handle the download after a real click.

* Chrome's download directory is changed at runtime via the CDP command
  Page.setDownloadBehavior so that every bid lands in its own sub-folder
  without restarting the driver.

* If the page has no attachment table (or zero .file-link elements) the
  function returns an empty list — no error is raised.
"""

import logging
import os
import re
import threading
import time
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger(__name__)

# Maximum seconds to wait for a single file to finish downloading
_DOWNLOAD_TIMEOUT = 60
# Poll interval while waiting for .crdownload to disappear
_POLL_INTERVAL = 0.5


def _sanitise_notice_id(notice_id: str) -> str:
    """Strip characters that are invalid in directory names."""
    return re.sub(r'[\\/:*?"<>|]', "_", notice_id).strip() or "unknown"


def _set_download_dir(driver, directory: str) -> None:
    """
    Use Chrome DevTools Protocol to redirect future downloads to *directory*.
    Works in both headed and headless (--headless=new) Chrome.
    """
    driver.execute_cdp_cmd(
        "Page.setDownloadBehavior",
        {"behavior": "allow", "downloadPath": str(directory)},
    )


def _wait_for_download(
    download_dir: Path,
    timeout: int = _DOWNLOAD_TIMEOUT,
    stop_event: threading.Event | None = None,
) -> bool:
    """
    Block until no .crdownload / .tmp files remain in *download_dir*.
    Returns True if the download completed within *timeout* seconds.
    Returns False immediately if *stop_event* is set.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if stop_event and stop_event.is_set():
            logger.info("  Download interrupted by stop signal.")
            return False
        in_progress = list(download_dir.glob("*.crdownload")) + list(download_dir.glob("*.tmp"))
        if not in_progress:
            return True
        time.sleep(_POLL_INTERVAL)
    return False


def download_attachments(
    driver,
    notice_id: str,
    base_temp_dir: Path,
    stop_event: threading.Event | None = None,
) -> list[str]:
    """
    Download all public attachments visible on the currently-loaded
    SAM.gov detail page.

    Parameters
    ----------
    driver        : active Selenium WebDriver (already on the detail page)
    notice_id     : SAM.gov opportunity notice ID — used as the sub-folder name
    base_temp_dir : absolute path to  scrappers/sam/temp_docs/
    stop_event    : optional threading.Event checked between downloads

    Returns
    -------
    List of filenames that were downloaded (may be empty).
    """
    # ── Locate attachment links ──────────────────────────────────────────────
    try:
        file_links = driver.find_elements(By.CSS_SELECTOR, "a.file-link")
    except Exception as exc:
        logger.debug(f"Could not query file-link elements: {exc}")
        return []

    if not file_links:
        logger.debug("No attachments found on this page — skipping download.")
        return []

    # ── Prepare the per-bid download folder ─────────────────────────────────
    safe_id = _sanitise_notice_id(notice_id)
    download_dir = base_temp_dir / safe_id
    download_dir.mkdir(parents=True, exist_ok=True)

    # Point Chrome at the folder before the first click
    _set_download_dir(driver, str(download_dir.resolve()))

    downloaded: list[str] = []

    for link in file_links:
        # Check stop signal before each file
        if stop_event and stop_event.is_set():
            logger.info("  Stop signal — aborting remaining downloads.")
            break

        filename = link.text.strip()
        if not filename:
            continue

        logger.info(f"  Downloading attachment: {filename}")
        try:
            # Scroll the element into view so it is clickable
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
            time.sleep(0.3)

            link.click()

            # Wait for Chrome to finish the download
            finished = _wait_for_download(download_dir, stop_event=stop_event)
            if finished:
                downloaded.append(filename)
                logger.info(f"  [OK] {filename}")
            else:
                logger.warning(f"  [TIMEOUT] {filename} did not finish within {_DOWNLOAD_TIMEOUT}s")

            # Brief pause between downloads to avoid overwhelming the server
            time.sleep(0.5)

        except Exception as exc:
            logger.warning(f"  [ERROR] Failed to download '{filename}': {exc}")
            continue

    logger.info(
        f"Downloaded {len(downloaded)}/{len(file_links)} attachment(s) "
        f"for notice {notice_id} -> {download_dir}"
    )
    return downloaded
