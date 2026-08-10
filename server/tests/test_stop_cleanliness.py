"""Stopping a run should look like a stop, not a crash.

From a real stopped BidNet run: the status was correct ("stopped"), but the log
carried an ERROR traceback under "failed", and a dozen urllib3 "Connection
refused" retries as the run tried to screenshot and re-quit a browser that
stopping had already closed.

    server/.venv/bin/python -m pytest server/tests/test_stop_cleanliness.py
"""

import ast
import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import run_manager  # noqa: E402
from app.core.base_scraper import BaseScraper, StopRequested  # noqa: E402


class FakeDriver:
    def __init__(self):
        self.quits = 0
        self.screenshots = 0
        self.frames = 0

    def quit(self):
        self.quits += 1

    def save_screenshot(self, _path):
        self.screenshots += 1
        return True

    def get_screenshot_as_base64(self):
        self.frames += 1
        return "frame"

    def find_element(self, *_a, **_k):
        return object()


@pytest.fixture
def scraper():
    run = run_manager.create_run("demo", Path("/tmp"))
    instance = BaseScraper(run["run_id"])
    instance.driver = FakeDriver()
    return instance


def test_stopping_quits_the_browser_exactly_once(scraper):
    driver = scraper.driver
    scraper.stop()
    scraper.cleanup()          # what the run's finally block does next
    assert driver.quits == 1   # not re-quit against a socket that is gone
    assert scraper.driver is None


def test_no_screenshot_is_attempted_after_a_stop(scraper):
    driver = scraper.driver
    scraper.stop()
    scraper.screenshot("fatal")
    assert driver.screenshots == 0


def test_no_live_frame_is_grabbed_after_a_stop(scraper):
    driver = scraper.driver
    scraper.stop()
    assert scraper.get_screenshot_base64() is None
    assert driver.frames == 0


def test_a_screenshot_still_works_on_a_healthy_run(scraper):
    scraper.screenshot("fatal")
    assert scraper.driver.screenshots == 1


def test_the_driver_reference_survives_a_stop(scraper):
    """Deliberate: clearing it would turn whatever Selenium call the worker is
    mid-way through into an AttributeError, which the scrapers'
    `except WebDriverException` handlers do not catch — so a clean stop would
    surface as a crash."""
    scraper.stop()
    assert scraper.driver is not None
    assert scraper.driver.find_element() is not None   # not None.find_element


def test_a_stop_is_still_raised_at_the_next_checkpoint(scraper):
    scraper.stop()
    with pytest.raises(StopRequested):
        scraper.raise_if_stopped()


# -- the scraper's own handling ----------------------------------------------


def test_bidnet_logs_a_stop_as_a_stop_not_a_failure(caplog, monkeypatch, tmp_path):
    """The run's status was always right — run_manager locks a stopped run — but
    the log said "failed" with a traceback, and it tried to screenshot a browser
    that stopping had already closed. Driven for real: a run interrupted at its
    first step should be quiet."""
    from app.scrapers.bidnet import scraper as bidnet
    from app.scrapers.bidnet.scraper import BidnetScraper

    caplog.set_level(logging.INFO)
    run = run_manager.create_run("bidnet", tmp_path, {"niche": "x", "niche_label": "X"})
    instance = BidnetScraper(run["run_id"], ["alpha"], None, "X")

    shots: list[str] = []
    monkeypatch.setattr(instance, "screenshot", lambda name: shots.append(name))
    monkeypatch.setattr(instance, "_save_run_row", lambda: None)
    monkeypatch.setattr(instance, "cleanup", lambda: None)
    # The user pressed Stop while the browser was starting.
    monkeypatch.setattr(instance, "start_driver", _raise_stop)
    monkeypatch.setattr(bidnet.run_manager, "remove_empty_folder", lambda run_id: None)

    instance.run()          # must not raise

    messages = [record.message for record in caplog.records]
    assert any("stopped by user" in message for message in messages)
    assert not any("failed" in message for message in messages)
    assert shots == [], "no screenshot: stopping already closed the browser"
    assert run_manager.get_run(run["run_id"])["errors"] == []


def _raise_stop(*_args, **_kwargs):
    raise StopRequested("run stopped by user")


# BidNet is driven for real above; the same defect existed in all eight portals,
# so the invariant is checked structurally across every scraper: a stop must be
# caught on its own before the catch-all, which logs a traceback under "failed"
# and screenshots a browser that stopping has already closed.
SCRAPERS = sorted(Path(__file__).parent.parent.glob("app/scrapers/*/scraper.py"))


@pytest.mark.parametrize("path", SCRAPERS, ids=lambda p: p.parent.name)
def test_every_scraper_run_catches_a_stop_before_the_catch_all(path):
    run_body = _find_run_method(path)
    # Only the try/except wrapping the whole flow. Nested ones (a best-effort DB
    # save, say) catch Exception around calls that raise no StopRequested.
    for node in run_body.body:
        if not isinstance(node, ast.Try):
            continue
        names = [_handler_name(handler) for handler in node.handlers]
        if "Exception" not in names:
            continue
        assert "StopRequested" in names, (
            f"{path.parent.name}: run() catches Exception with no StopRequested "
            f"handler — a stopped run is logged as a failure"
        )
        assert names.index("StopRequested") < names.index("Exception"), (
            f"{path.parent.name}: StopRequested is handled after Exception, so it "
            f"never gets there"
        )


def _find_run_method(path: Path) -> ast.FunctionDef:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            return node
    raise AssertionError(f"{path} has no run() method")


def _handler_name(handler: ast.ExceptHandler) -> str | None:
    kind = handler.type
    if isinstance(kind, ast.Name):
        return kind.id
    return None
