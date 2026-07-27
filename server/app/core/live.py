"""Registry of live, in-flight scrapers keyed by run_id, so the shared
live-screenshot endpoint can grab a frame from a running browser on demand.

A registered object only needs a ``get_screenshot_base64()`` method returning a
base64-encoded PNG (or None). Registration and capture are thread-safe and
best-effort: a failed frame returns None and never affects the scrape. Frames
are only ever captured while a client is actively watching (the Live Preview
modal polls this), so the driver is left alone the rest of the time.
"""

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_live: dict[str, Any] = {}


def register(run_id: str, scraper: Any) -> None:
    """Mark a scraper as the live browser for `run_id`."""
    with _lock:
        _live[run_id] = scraper


def unregister(run_id: str) -> None:
    """Drop a run's live scraper (safe to call more than once)."""
    with _lock:
        _live.pop(run_id, None)


def stop(run_id: str) -> bool:
    """Interrupt a run's live browser so a blocked Selenium call unwinds now.

    Prefers the scraper's own ``stop()`` (BaseScraper quits its driver and sets a
    cooperative flag); failing that, quits a bare ``driver`` if the registered
    object exposes one. Returns True if something was interrupted. Never raises —
    the run-state side of stopping is handled separately by run_manager.
    """
    with _lock:
        scraper = _live.get(run_id)
    if scraper is None:
        return False
    stopper = getattr(scraper, "stop", None)
    if callable(stopper):
        try:
            stopper()
            return True
        except Exception:  # noqa: BLE001 — best-effort teardown
            logger.debug("live stop() failed for run %s", run_id, exc_info=True)
    driver = getattr(scraper, "driver", None)
    if driver is not None:
        try:
            driver.quit()
            return True
        except Exception:  # noqa: BLE001 — the driver may already be gone
            logger.debug("live driver.quit() failed for run %s", run_id, exc_info=True)
    return False


def capture(run_id: str) -> str | None:
    """Return a base64 PNG frame of the run's live browser, or None.

    Never raises: if the run isn't registered, the driver isn't up yet, or the
    capture fails, it returns None so the caller just shows "waiting".
    """
    with _lock:
        scraper = _live.get(run_id)
    if scraper is None:
        return None
    grab = getattr(scraper, "get_screenshot_base64", None)
    if grab is None:
        return None
    try:
        return grab()
    except Exception:  # noqa: BLE001 — a failed frame must never affect the scrape
        logger.debug("live screenshot capture failed for run %s", run_id, exc_info=True)
        return None
