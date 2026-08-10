"""Shared test setup.

The debug pacing exists so a human can follow a visible browser; a test suite has
no one watching, and paying 1.5s per interaction made the BidNet keyword tests
three seconds slower each the moment headed mode went on. Zeroed here rather than
stubbed per test, so a new test never silently inherits the delay — and so the
production default stays whatever `scraper.py` says it is.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _no_debug_pacing(monkeypatch):
    from app.scrapers.bidnet import scraper as bidnet

    monkeypatch.setattr(bidnet, "DEBUG_PAUSE_SECONDS", 0)
