"""Core exception classes for scrapers."""

import time
import threading


class StopScraping(Exception):
    """Raised to immediately halt the scraper when requested by the user."""
    pass


def check_stop(stop_event: threading.Event | None = None):
    """Raises StopScraping if the stop_event is set."""
    if stop_event and stop_event.is_set():
        raise StopScraping("Scraping stopped by user request.")


def smart_sleep(seconds: float, stop_event: threading.Event | None = None):
    """
    Sleeps for the given duration but polls the stop_event every 0.1s.
    Breaks immediately via StopScraping exception if the event is set mid-sleep.
    """
    if not stop_event:
        time.sleep(seconds)
        return

    check_stop(stop_event)
    end_time = time.time() + seconds
    while time.time() < end_time:
        check_stop(stop_event)
        time.sleep(min(0.1, end_time - time.time()))
