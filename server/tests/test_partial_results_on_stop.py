"""Stopping a run keeps what it had already found.

Stop used to cost a run everything. The cause was structural rather than a bug
anyone wrote: every scraper does its work in a loop and then, *after* the loop,
saves to the database, writes the sheet and packages the archive. `StopRequested`
unwinds out of the loop, so it unwinds past all three — a 300-bid run stopped at
bid 75 delivered none of the 75.

Two portals had already noticed and worked around it, and the shape of that
workaround is the reason this is a refactor and not a patch: SAM and the
MyFlorida sweep both flushed on stop and then wrote **status="completed"**,
because the download endpoint would serve nothing else. That is the wrong lie.
The run did not complete, and a console that says it did hides the one thing the
reviewer needs to know — that rows are missing. A stopped run now stays stopped
and carries `partial_results` instead.

    server/.venv/bin/python -m pytest server/tests/test_partial_results_on_stop.py
"""

import importlib
import inspect
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main  # noqa: E402
from app.core import run_manager  # noqa: E402
from app.core.base_scraper import BaseScraper  # noqa: E402

# Every portal whose runs produce rows, and the class that owns each one's stop
# path. Cal eProcure is deliberately absent: it downloads nothing and is in the
# console's NO_DOWNLOAD set, so there is no partial result for it to keep.
BASE_SCRAPERS = [
    ("myflorida",       "app.scrapers.myflorida.scraper",       "MFMPScraper"),
    ("myflorida_sweep", "app.scrapers.myflorida.sweep.scraper", "SweepScraper"),
    ("bidnet",          "app.scrapers.bidnet.scraper",          "BidnetScraper"),
    ("bidnet_member",   "app.scrapers.bidnet.member_agencies",  "MemberAgencySweepScraper"),
    ("septa",           "app.scrapers.septa.scraper",           "SeptaScraper"),
    ("ridemetro",       "app.scrapers.ridemetro.scraper",       "RideMetroScraper"),
    ("philadelphia",    "app.scrapers.philadelphia.scraper",    "PhiladelphiaScraper"),
    ("emma",            "app.scrapers.emma.scraper",            "EmmaScraper"),
    ("wisconsin",       "app.scrapers.wisconsin.scraper",       "WisconsinScraper"),
    ("northdakota",     "app.scrapers.northdakota.scraper",     "NorthDakotaScraper"),
]
RUNNERS = [("sam", "app.scrapers.sam.runner"), ("unison", "app.scrapers.unison.runner")]


def _cls(module: str, name: str):
    return getattr(importlib.import_module(module), name)


# -- the audit --------------------------------------------------------------


@pytest.mark.parametrize("portal,module,name", BASE_SCRAPERS, ids=[p for p, _, _ in BASE_SCRAPERS])
def test_every_portal_saves_its_rows_when_stopped(portal, module, name):
    """The audit this refactor was asked for. A portal that inherits the base's
    no-op keeps nothing, and the failure is silent — the run just ends."""
    assert "flush_partial" in _cls(module, name).__dict__, (
        f"{portal} inherits the base no-op and would still discard its rows"
    )


@pytest.mark.parametrize("portal,module,name", BASE_SCRAPERS, ids=[p for p, _, _ in BASE_SCRAPERS])
def test_every_portal_delivers_on_the_stop_path(portal, module, name):
    """Implementing the flush is half of it — the stop handler has to call it."""
    source = inspect.getsource(_cls(module, name).run)
    assert "self.deliver_partial()" in source, f"{portal}'s stop handler drops its work"


@pytest.mark.parametrize("portal,module", RUNNERS, ids=[p for p, _ in RUNNERS])
def test_the_function_runners_record_their_partial(portal, module):
    """SAM and Unison are not BaseScraper subclasses — they finalize inline and
    mark the run themselves."""
    source = inspect.getsource(importlib.import_module(module).execute_run)
    assert "mark_partial" in source


@pytest.mark.parametrize("portal,module", RUNNERS, ids=[p for p, _ in RUNNERS])
def test_a_stopped_run_no_longer_claims_it_completed(portal, module):
    """The workaround this replaces. Both runners used to write
    status="completed" on a stopped run so the download would be served."""
    source = inspect.getsource(importlib.import_module(module).execute_run)
    completed = [
        line for line in source.splitlines()
        if 'status="completed"' in line
    ]
    for line in completed:
        assert "stopped" not in line, f"{portal} still marks a stopped run completed"


def test_the_base_default_keeps_nothing_and_says_so():
    """A portal that has not been taught this yet must not silently appear to
    work. The default returns 0, which is the honest answer."""
    assert BaseScraper.flush_partial(object()) == 0


# -- what a stopped run records ---------------------------------------------


def _run(tmp_path, **extra) -> str:
    run = run_manager.create_run("septa", tmp_path, {"search": "x", **extra})
    return run["run_id"]


def test_marking_a_partial_leaves_the_run_stopped(tmp_path):
    """The heart of it. `request_stop` locked the status; nothing the worker
    writes on its way out may reopen that question."""
    run_id = _run(tmp_path)
    run_manager.update_run(run_id, status="running")
    run_manager.request_stop(run_id)

    run_manager.mark_partial(run_id, 75)

    stored = run_manager.get_run(run_id)
    assert stored["status"] == "stopped"
    assert stored["partial_results"] is True
    assert stored["partial_record_count"] == 75


def test_a_stopped_run_that_kept_nothing_is_not_marked(tmp_path):
    """Stopped before it found anything. The console must not offer a download
    that 409s when pressed."""
    run_id = _run(tmp_path)
    run_manager.update_run(run_id, status="running")
    run_manager.request_stop(run_id)

    assert "partial_results" not in run_manager.get_run(run_id)


def test_deliver_partial_records_what_the_flush_kept(tmp_path):
    """The base's orchestration: flush, package, mark — in that order, and the
    count the flush reports is the count the run records."""
    run_id = _run(tmp_path)
    scraper = BaseScraper(run_id)
    scraper.flush_partial = lambda: 42

    scraper.deliver_partial()

    assert run_manager.get_run(run_id)["partial_record_count"] == 42


def test_a_flush_that_raises_still_packages_the_run(tmp_path):
    """A stopped run is unwinding through a browser being torn down. A flush
    that dies must not also cost the documents already on disk."""
    run_id = _run(tmp_path)
    scraper = BaseScraper(run_id)

    def boom() -> int:
        raise RuntimeError("half-parsed page")

    scraper.flush_partial = boom
    scraper.deliver_partial()  # must not raise

    stored = run_manager.get_run(run_id)
    assert stored["partial_results"] is True
    assert stored["partial_record_count"] == 0


# -- the download gate ------------------------------------------------------


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def test_a_stopped_run_with_results_is_downloadable(client, tmp_path):
    """The gate that made all of this invisible: the endpoint served
    "completed" and nothing else, which is why two portals lied about it."""
    run_id = _run(tmp_path)
    run_manager.update_run(run_id, status="stopped", partial_results=True)

    # Past the status gate — 404 here is "no artifact in this bare fixture",
    # not "a stopped run may not be downloaded", which is what 409 would mean.
    assert client.get(f"/runs/{run_id}/download").status_code != 409


def test_a_stopped_run_with_nothing_is_refused_with_a_reason(client, tmp_path):
    run_id = _run(tmp_path)
    run_manager.update_run(run_id, status="stopped")

    response = client.get(f"/runs/{run_id}/download")

    assert response.status_code == 409
    assert "stopped before it gathered any results" in response.json()["detail"]


def test_a_running_run_is_still_refused(client, tmp_path):
    run_id = _run(tmp_path)
    run_manager.update_run(run_id, status="running")

    assert client.get(f"/runs/{run_id}/download").status_code == 409


def test_the_jobs_list_reports_the_partial_flag(client, tmp_path):
    """The console's jobs bar holds a just-stopped row and shows its Download
    the moment the flush lands, which it can only do if the flag is on the row."""
    run_id = _run(tmp_path)
    run_manager.update_run(run_id, status="running")

    jobs = client.get("/runs?active=true").json()["jobs"]
    row = next((j for j in jobs if j["run_id"] == run_id), None)

    assert row is not None
    assert "partial_results" in row
    assert "partial_record_count" in row


# -- packaging twice --------------------------------------------------------


def test_a_packaged_run_is_not_repackaged_over(tmp_path):
    """Two callers reach `archive_run` for one stopped run: the portal's own
    `_finalize` (MyFlorida's sweep archives inside it) and `deliver_partial`.
    The second used to rebuild from a workspace the first had deleted, writing
    an almost-empty ZIP over a good one."""
    from app.core import exports

    run_id = _run(tmp_path)
    archive = tmp_path / "already.zip"
    archive.write_bytes(b"the good archive")
    gone = tmp_path / "deleted-workspace"
    run_manager.update_run(run_id, folder=str(gone), zip_path=str(archive))

    assert exports.archive_run(run_id) == str(archive)
    assert archive.read_bytes() == b"the good archive"


def test_a_live_workspace_is_still_repackaged(tmp_path):
    """The guard keys on the workspace being gone, not on an artifact existing —
    BidNet deliberately re-archives a session root that is still on disk."""
    from app.core import exports

    run_id = _run(tmp_path)
    workspace = tmp_path / "live"
    workspace.mkdir()
    (workspace / "sheet.xlsx").write_bytes(b"rows")
    archive = tmp_path / "old.zip"
    archive.write_bytes(b"stale")
    run_manager.update_run(run_id, folder=str(workspace), zip_path=str(archive))

    assert exports.archive_run(run_id) != str(archive) or archive.read_bytes() != b"stale"
