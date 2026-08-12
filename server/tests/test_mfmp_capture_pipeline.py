"""MFMP after the matrix: human OTP, every bid kept, one organised archive.

Three changes, three things worth holding:

* **Login waits for a person.** The portal answers a correct password with a
  one-time code. Leaving `/login` is not being signed in — the OTP challenge is
  its own route — so the old check handed a half-authenticated session to the
  search step, which failed later with a message about a missing button.
* **Nothing is scored, categorised or dropped.** Every advertisement the portal
  returns reaches the summary sheet with its documents beside it. What is worth
  pursuing is the reviewer's call, and the scraper no longer pre-empts it.
* **The archive has a shape.** `MyFlorida_Export/` with the summary at its root
  and one folder per bid under `Bids_Data/`, so a row in the sheet names a
  folder the reader can actually open.

    server/.venv/bin/python -m pytest server/tests/test_mfmp_capture_pipeline.py
"""

import logging
import os
import sys
import zipfile
from pathlib import Path

import pytest
from selenium.common.exceptions import TimeoutException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import run_manager  # noqa: E402
from app.scrapers.myflorida import storage, workbook  # noqa: E402
from app.scrapers.myflorida import scraper as mfmp  # noqa: E402
from app.scrapers.myflorida.scraper import LoginTimeout, MFMPScraper  # noqa: E402


# =============================================================================
# The OTP window
# =============================================================================


class FakePortal:
    """A browser that reaches the dashboard after `steps` polls, if ever."""

    def __init__(self, steps: int, url: str = "https://vendor.myfloridamarketplace.com/otp"):
        self.polls = 0
        self.steps = steps
        self.current_url = url

    def find_elements(self, _by, _value):
        self.polls += 1
        if self.polls >= self.steps:
            self.current_url = "https://vendor.myfloridamarketplace.com/vendor/ads"
            return [object()]
        return []


def _scraper(tmp_path, monkeypatch, driver):
    run = run_manager.create_run("myflorida", tmp_path, {"category": "x"})
    instance = MFMPScraper(run["run_id"], codes=["10-11"])
    instance.driver = driver
    monkeypatch.setattr(instance, "screenshot", lambda name: None)
    return instance


class ImmediateWait:
    """Polls the condition without sleeping, then times out like Selenium's."""

    def __init__(self, driver, timeout, attempts=5):
        self.driver, self.timeout, self.attempts = driver, timeout, attempts

    def until(self, condition):
        for _ in range(self.attempts):
            if condition(self.driver):
                return True
        raise TimeoutException(f"gave up after {self.timeout}s")


def test_the_run_waits_for_the_one_time_password(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(mfmp.settings, "mfmp_manual_otp", True)
    monkeypatch.setattr(mfmp.settings, "mfmp_otp_wait_seconds", 120)
    driver = FakePortal(steps=3)
    instance = _scraper(tmp_path, monkeypatch, driver)
    seen: list[int] = []
    monkeypatch.setattr(instance, "wait", lambda t=None: (seen.append(t), ImmediateWait(driver, t))[1])

    instance._await_authenticated()

    assert seen == [120], "the OTP window, not the default element wait"
    run = run_manager.get_run(instance.run_id)
    assert run["awaiting_otp"] is False, "the flag is cleared once through"
    assert any("one-time password" in w for w in run["warnings"]), (
        "the prompt must reach the run — nobody is reading the server's stdout"
    )


def test_leaving_the_login_page_is_not_being_signed_in(tmp_path, monkeypatch):
    """The OTP challenge is its own route, so a URL check alone passes the moment
    the code is *demanded* — and hands a half-authenticated session onwards."""
    driver = FakePortal(steps=99, url="https://vendor.myfloridamarketplace.com/otp")
    instance = _scraper(tmp_path, monkeypatch, driver)

    assert instance._authenticated(driver) is False, "off /login, but not inside"


def test_the_dashboard_is_what_counts_as_signed_in(tmp_path, monkeypatch):
    driver = FakePortal(steps=1, url="https://vendor.myfloridamarketplace.com/vendor/ads")
    instance = _scraper(tmp_path, monkeypatch, driver)

    assert instance._authenticated(driver) is True


def test_a_code_that_never_arrives_fails_with_something_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr(mfmp.settings, "mfmp_manual_otp", True)
    monkeypatch.setattr(mfmp.settings, "mfmp_otp_wait_seconds", 90)
    driver = FakePortal(steps=999)
    instance = _scraper(tmp_path, monkeypatch, driver)
    monkeypatch.setattr(instance, "wait", lambda t=None: ImmediateWait(driver, t))

    with pytest.raises(LoginTimeout) as excinfo:
        instance._await_authenticated()

    message = str(excinfo.value)
    assert "90s" in message
    assert "Chrome window" in message, "say what a human should have done"
    assert run_manager.get_run(instance.run_id)["awaiting_otp"] is False


def test_with_manual_otp_off_the_wait_is_the_ordinary_one(tmp_path, monkeypatch):
    """An account without OTP should not sit for two minutes on every login."""
    monkeypatch.setattr(mfmp.settings, "mfmp_manual_otp", False)
    driver = FakePortal(steps=1)
    instance = _scraper(tmp_path, monkeypatch, driver)
    seen: list[int] = []
    monkeypatch.setattr(instance, "wait", lambda t=None: (seen.append(t), ImmediateWait(driver, t))[1])

    instance._await_authenticated()

    assert seen == [mfmp.LOGIN_FORM_TIMEOUT]
    assert "one-time password" not in " ".join(
        run_manager.get_run(instance.run_id)["warnings"]
    )


def test_the_browser_is_visible_while_manual_otp_is_on(tmp_path, monkeypatch):
    """There is nothing to type a code into in a headless window, and the run's
    own Live-preview flag must not decide it."""
    monkeypatch.setattr(mfmp.settings, "mfmp_manual_otp", True)
    run = run_manager.create_run("myflorida", tmp_path, {})
    instance = MFMPScraper(run["run_id"], codes=[])
    calls: list[dict] = []
    monkeypatch.setattr(instance, "start_driver", lambda **kw: calls.append(kw))
    monkeypatch.setattr(instance, "login", _stop)
    monkeypatch.setattr(instance, "cleanup", lambda: None)
    monkeypatch.setattr(mfmp.run_manager, "remove_empty_folder", lambda run_id: None)

    instance.run()

    assert calls == [{"headless": False}]


def _stop(*_args, **_kwargs):
    from app.core.base_scraper import StopRequested

    raise StopRequested("far enough — the driver call is what this test is about")


# =============================================================================
# Nothing is dropped
# =============================================================================


def test_the_close_date_filter_is_gone_from_the_pipeline():
    """It pruned the workbook to ads at least N days from closing, which removed
    bids a reviewer might still have wanted. Judging what is worth pursuing is
    the reviewer's job now, so the call and the function are both gone."""
    from app.scrapers.myflorida import ingest

    assert not hasattr(ingest, "filter_workbook_by_close_date")
    source = Path(mfmp.__file__).read_text()
    assert "from app.core.closing_filter import" not in source, (
        "the flow should no longer import the close-date rule at all"
    )


def test_the_sweep_no_longer_imports_the_classifier():
    """The matrix is out of the pipeline. The modules still exist — they serve
    the lane views of runs recorded before this change — but nothing in the
    scraping path reaches for them."""
    source = Path(
        Path(mfmp.__file__).parent / "sweep" / "scraper.py"
    ).read_text()

    for banned in ("from app.scrapers.myflorida.sweep.routing import",
                   "from app.scrapers.myflorida.sweep.scoring import",
                   "classify(", "get_config()"):
        assert banned not in source, f"the sweep still reaches for {banned!r}"


def test_the_sweep_delivers_a_zip_not_a_bare_sheet():
    """It kept nothing but a workbook while its documents were deleted after
    scoring. It keeps them now, so the deliverable is the archive."""
    from app.core import exports

    assert "myflorida_sweep" not in exports.EXCEL_ONLY_PORTALS
    assert "myflorida_sweep" in exports.DOC_PORTALS


# =============================================================================
# The archive's shape
# =============================================================================


def test_the_layout_is_one_root_with_the_summary_at_its_top(tmp_path):
    root = storage.export_root(tmp_path)

    assert root.name == "MyFlorida_Export"
    assert storage.summary_path(tmp_path).name == "MyFlorida_Bids_Summary.xlsx"
    assert storage.summary_path(tmp_path).parent == root


def test_each_bid_gets_a_folder_named_for_its_ad_number(tmp_path):
    folder = storage.bid_folder(tmp_path, "DMS-21/22-001", "Statewide signage services")

    assert folder.is_dir()
    assert folder.parent.name == "Bids_Data"
    assert "DMS" in folder.name and "signage" not in folder.name, (
        "the ad number addresses the folder; the title is in the sheet"
    )


def test_a_bid_with_no_ad_number_still_gets_somewhere_to_live(tmp_path):
    folder = storage.bid_folder(tmp_path, "", "An advertisement with no number")

    assert folder.is_dir()
    assert folder.name


def test_the_folder_column_points_inside_the_archive():
    """An absolute server path would be useless to whoever unpacks the ZIP."""
    reference = storage.folder_reference("RFP-2026-114", "Anything")

    assert reference == "Bids_Data/RFP-2026-114"
    assert not reference.startswith("/")


def test_the_summary_holds_every_captured_bid(tmp_path):
    from openpyxl import load_workbook

    records = [
        {"ad_number": f"AD-{n}", "title": f"Bid {n}", "agency": "DMS",
         "close_date": "2026-09-01", "documents": ["a.pdf", "b.pdf"],
         "folder": f"Bids_Data/AD-{n}"}
        for n in range(5)
    ]
    path = workbook.build_from_records(records, tmp_path)
    sheet = load_workbook(path).active
    rows = list(sheet.iter_rows(values_only=True))

    assert path == storage.summary_path(tmp_path)
    assert len(rows) == 6, "a header and five bids — none filtered out"
    headers = [str(h) for h in rows[0]]
    for expected in ("Title", "Ad Number", "Agency", "Closing Date", "Documents", "Folder"):
        assert expected in headers
    assert rows[1][headers.index("Documents")] == 2
    assert rows[1][headers.index("Folder")] == "Bids_Data/AD-0"


def test_the_zip_is_the_export_root_with_documents_in_place(tmp_path, monkeypatch):
    """End to end on the packaging: what a reviewer downloads unpacks to one
    folder, with the sheet at the top and each bid's files under its own name."""
    from app.core import exports

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    storage.summary_path(run_dir).write_text("summary")
    (storage.bid_folder(run_dir, "AD-1") / "Solicitation.pdf").write_text("one")
    (storage.bid_folder(run_dir, "AD-2") / "Specs.docx").write_text("two")
    # Staging that must never reach the archive.
    (run_dir / "_exports").mkdir()
    (run_dir / "_exports" / "page1.xlsx").write_text("raw")
    (run_dir / "_downloads").mkdir()
    (run_dir / "_downloads" / "part.crdownload").write_text("mid-flight")

    run = {
        "run_id": "abc123", "scraper": "myflorida_sweep", "folder": str(run_dir),
        "excel_path": str(storage.summary_path(run_dir)), "search": "open",
    }
    monkeypatch.setattr(exports, "excel_bytes", lambda r: (b"regenerated", "MyFlorida_Sweep_(open).xlsx"))
    out = tmp_path / "out.zip"
    exports.build_zip(run, out)

    with zipfile.ZipFile(out) as zf:
        names = sorted(zf.namelist())

    assert names == [
        "MyFlorida_Export/Bids_Data/AD-1/Solicitation.pdf",
        "MyFlorida_Export/Bids_Data/AD-2/Specs.docx",
        "MyFlorida_Export/MyFlorida_Bids_Summary.xlsx",
    ], names


def test_the_summary_is_not_shipped_twice(tmp_path, monkeypatch):
    """The regenerated sheet comes back under a different name than the copy in
    the tree, so a name check alone would add a second one at the ZIP root."""
    from app.core import exports

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    storage.summary_path(run_dir).write_bytes(b"the sheet")
    run = {
        "run_id": "abc123", "scraper": "myflorida", "folder": str(run_dir),
        "excel_path": str(storage.summary_path(run_dir)),
    }
    monkeypatch.setattr(exports, "excel_bytes", lambda r: (b"same rows", "MyFlorida_(x).xlsx"))
    out = tmp_path / "out.zip"
    exports.build_zip(run, out)

    with zipfile.ZipFile(out) as zf:
        assert zf.namelist() == ["MyFlorida_Export/MyFlorida_Bids_Summary.xlsx"]
