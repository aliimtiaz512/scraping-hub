"""Several niches in one execution, with nothing leaking between them.

The reported failure: scraping a niche produced output containing records and
documents from niches run earlier. Three separate paths caused it, and these
tests pin all three shut — closing any two of them still leaks:

* the niche's workspace folder was reused, so an earlier run's files were still
  in it when the next run was packaged;
* its spreadsheet was regenerated from *every* run of that niche in the session;
* its ZIP was the whole day's session root, every niche in it.

Plus the batch contract itself: sequential, config-driven, and one niche failing
does not end the execution.

    server/.venv/bin/python -m pytest server/tests/test_bidnet_batch.py
"""

import logging
import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import run_manager  # noqa: E402
from app.scrapers.bidnet import batch as batch_module  # noqa: E402
from app.scrapers.bidnet import storage  # noqa: E402
from app.scrapers.bidnet.batch import NicheJob, StateResetter, config_loader  # noqa: E402
from app.scrapers.bidnet.filters import SidebarFilterRequest  # noqa: E402
from app.scrapers.bidnet.niches import KIND_NIGP  # noqa: E402

CONFIG = {
    "IT_Services": {"keywords": ["cloud migration", "cybersecurity"], "nigp_codes": ["91828", "92000"]},
    "Construction": {"keywords": ["paving", "roofing"], "nigp_codes": ["91319", "91400"]},
}


# =============================================================================
# config_loader
# =============================================================================


def test_a_config_dict_becomes_one_job_per_niche():
    jobs = config_loader(config=CONFIG)

    assert [job.key for job in jobs] == ["IT_Services", "Construction"]
    assert jobs[0].keywords == ["cloud migration", "cybersecurity"]
    assert jobs[0].nigp_codes == ["91828", "92000"]


def test_a_jobs_terms_are_keywords_first_then_codes():
    """The scraper searches the queue in order, and a bid's `Matched Keyword`
    should lead with the human-readable term that found it."""
    job = config_loader(config=CONFIG)[0]
    terms = job.search_terms()

    assert [t.term for t in terms] == ["cloud migration", "cybersecurity", "91828", "92000"]
    assert [t.kind for t in terms].count(KIND_NIGP) == 2
    assert [t.kind for t in terms][-2:] == [KIND_NIGP, KIND_NIGP]


def test_blank_and_duplicate_terms_are_dropped():
    jobs = config_loader(config={"X": {"keywords": ["  paving ", "paving", "", None], "nigp_codes": []}})

    assert jobs[0].keywords == ["paving"]


def test_a_niche_with_nothing_to_search_is_not_a_job(caplog):
    """It would open a browser, log in, and search nothing."""
    caplog.set_level(logging.WARNING)
    jobs = config_loader(config={"Empty": {"keywords": [], "nigp_codes": []}, **CONFIG})

    assert [job.key for job in jobs] == ["IT_Services", "Construction"]
    assert any("no terms" in r.getMessage() for r in caplog.records)


# =============================================================================
# state_resetter
# =============================================================================


def test_the_niche_folder_is_emptied_before_the_niche_runs(tmp_path, caplog):
    """The leak, at its source: a niche folder that still holds the previous
    execution's spreadsheet and documents is packaged as if this run produced
    them."""
    caplog.set_level(logging.INFO)
    root = tmp_path / "BidNet_Batch_2026-08-11_120000"
    stale = root / "IT-Services"
    (stale / "documents" / "REF-999 - Old Bid").mkdir(parents=True)
    (stale / "documents" / "REF-999 - Old Bid" / "old.pdf").write_text("previous niche")
    (stale / "IT-Services_Bids.xlsx").write_text("previous spreadsheet")

    folder = StateResetter(root).reset(NicheJob(key="IT_Services", label="IT Services", slug="IT-Services"))

    assert folder == stale
    assert list(folder.iterdir()) == [], "the folder must start empty"
    assert any("[STATE RESET]" in r.getMessage() for r in caplog.records)


def test_the_reset_runs_for_the_first_niche_too(tmp_path):
    """A batch root is new, but the first niche must not be the one iteration
    that skips the check — a root can be reused by an operator re-running with
    the same name."""
    root = tmp_path / "batch"
    folder = StateResetter(root).reset(NicheJob(key="a", label="A"))

    assert folder.is_dir()


def test_the_previous_niches_browser_is_released(tmp_path):
    class FakeScraper:
        driver = object()
        cleaned = False

        def cleanup(self):
            type(self).cleaned = True

    resetter = StateResetter(tmp_path)
    resetter.adopt(FakeScraper())
    resetter.reset(NicheJob(key="b", label="B"))

    assert FakeScraper.cleaned is True


def test_a_browser_that_will_not_close_does_not_end_the_batch(tmp_path):
    class Stuck:
        driver = object()

        def cleanup(self):
            raise RuntimeError("chrome is wedged")

    resetter = StateResetter(tmp_path)
    resetter.adopt(Stuck())
    folder = resetter.reset(NicheJob(key="c", label="C"))     # must not raise

    assert folder.is_dir()


# =============================================================================
# The loop: sequential, isolated, resilient
# =============================================================================


@pytest.fixture
def batch_env(monkeypatch, tmp_path):
    """A batch whose scraper is replaced by one that writes a marker file, so
    what ends up in each niche's folder and ZIP is checkable.

    `work_root`/`archive_root` are read-only properties on Settings, so they are
    swapped on the class the way test_bidnet_storage does it.
    """
    from app.config import settings as app_settings

    work, archive = tmp_path / "work", tmp_path / "archive"
    work.mkdir()
    archive.mkdir()
    cls = type(app_settings)
    saved = (cls.work_root, cls.archive_root)
    cls.work_root = property(lambda s, p=work: p)
    cls.archive_root = property(lambda s, p=archive: p)

    ran: list[str] = []
    fail_for: set[str] = set()

    class FakeScraper:
        def __init__(self, run_id, terms, filters, niche_label=None):
            self.run_id, self.terms, self.niche_label = run_id, terms, niche_label
            self.driver = None

        def run(self):
            ran.append(self.niche_label)
            run = run_manager.get_run(self.run_id)
            folder = Path(run["folder"])
            if self.niche_label in fail_for:
                raise RuntimeError(f"{self.niche_label} blew up")
            # What a real run leaves behind: a sheet and a document.
            (folder / f"{folder.name}_Bids.xlsx").write_text(f"rows for {self.niche_label}")
            docs = folder / "documents" / f"REF-{len(ran)}"
            docs.mkdir(parents=True)
            (docs / "spec.pdf").write_text(self.niche_label)
            run_manager.update_run(self.run_id, status="completed", step="done")

        def cleanup(self):
            pass

    import app.scrapers.bidnet.scraper as scraper_module

    monkeypatch.setattr(scraper_module, "BidnetScraper", FakeScraper)
    # The DB regeneration is exercised in its own test; here the scraper's own
    # sheet stands, which is the behaviour when Postgres is unreachable.
    monkeypatch.setattr(
        batch_module, "refresh_niche_excel", lambda run_id, run: None
    )
    try:
        yield ran, fail_for, tmp_path
    finally:
        cls.work_root, cls.archive_root = saved


def _batch_run(jobs):
    """The parent run record a batch reports itself on."""
    run = run_manager.create_run("bidnet", Path("/tmp"), {"is_batch": True, "niche_total": len(jobs)})
    return run["run_id"]


def test_every_niche_runs_in_order(batch_env):
    ran, _, _ = batch_env
    jobs = config_loader(config=CONFIG)
    batch_module.execute_batch(_batch_run(jobs), jobs, SidebarFilterRequest())

    assert ran == ["IT_Services", "Construction"]


def test_each_niche_gets_its_own_folder_holding_only_its_own_output(batch_env):
    _, _, tmp_path = batch_env
    jobs = config_loader(config=CONFIG)
    batch_module.execute_batch(
        _batch_run(jobs), jobs, SidebarFilterRequest(), keep_workspace=True
    )

    root = next((tmp_path / "work").iterdir())
    folders = {p.name for p in root.iterdir()}
    assert folders == {"IT_Services", "Construction"}
    for name in folders:
        docs = list((root / name / "documents").rglob("*.pdf"))
        assert [d.read_text() for d in docs] == [name], f"{name} holds another niche's document"


def test_each_niche_is_zipped_on_its_own(batch_env):
    """The archive is where the leak was most visible: a ZIP downloaded after
    one niche used to contain every other niche run that day."""
    _, _, tmp_path = batch_env
    jobs = config_loader(config=CONFIG)
    batch_module.execute_batch(_batch_run(jobs), jobs, SidebarFilterRequest())

    zips = sorted(p.name for p in (tmp_path / "archive").glob("*.zip"))
    assert len(zips) == 3, f"one per niche plus the execution bundle: {zips}"

    per_niche = [p for p in (tmp_path / "archive").glob("*.zip") if "IT_Services" in p.name]
    assert len(per_niche) == 1
    with zipfile.ZipFile(per_niche[0]) as zf:
        names = zf.namelist()
    assert names, "the niche ZIP is empty"
    assert all(n.startswith("IT_Services/") for n in names), names
    assert not any("Construction" in n for n in names)


def test_the_execution_bundle_is_a_flat_zip_of_niche_spreadsheets(batch_env):
    """What "Run all niches" hands back: one dated ZIP, one sheet per niche, at
    its root. No folder per niche — the sheets are what the client opens."""
    from datetime import date

    _, _, tmp_path = batch_env
    jobs = config_loader(config=CONFIG)
    batch_module.execute_batch(_batch_run(jobs), jobs, SidebarFilterRequest())

    bundle = [p for p in (tmp_path / "archive").glob("*.zip") if "_IT" not in p.name and "_Con" not in p.name]
    assert len(bundle) == 1
    assert bundle[0].name == f"BidNet_Niche_Bids_{date.today().isoformat()}.zip", bundle[0].name
    with zipfile.ZipFile(bundle[0]) as zf:
        names = sorted(zf.namelist())
    assert names == ["Construction_Bids.xlsx", "IT_Services_Bids.xlsx"], names


def test_the_workspace_is_removed_when_the_batch_finishes(batch_env, caplog):
    caplog.set_level(logging.INFO)
    _, _, tmp_path = batch_env
    jobs = config_loader(config=CONFIG)
    batch_module.execute_batch(_batch_run(jobs), jobs, SidebarFilterRequest())

    assert list((tmp_path / "work").iterdir()) == []
    assert any("[CLEANUP]" in r.getMessage() for r in caplog.records)


def test_one_niche_failing_does_not_stop_the_batch(batch_env, caplog):
    """The whole point of the per-niche try/except: a portal timeout on niche 1
    must not cost niche 2."""
    caplog.set_level(logging.INFO)
    ran, fail_for, tmp_path = batch_env
    fail_for.add("IT_Services")
    jobs = config_loader(config=CONFIG)
    batch_id = _batch_run(jobs)

    result = batch_module.execute_batch(batch_id, jobs, SidebarFilterRequest())

    assert ran == ["IT_Services", "Construction"], "the second niche still ran"
    assert [o["status"] for o in result["niches"]] == ["failed", "completed"]
    # …and the failure is named on the batch rather than swallowed.
    errors = run_manager.get_run(batch_id)["errors"]
    assert any("IT_Services" in e for e in errors)
    assert any("FAILED" in r.getMessage() for r in caplog.records)


def test_a_failed_niche_contributes_nothing_to_the_others(batch_env):
    """A niche that died must not appear in another niche's archive, and must
    not ship one of its own when it produced nothing — that is the leak in its
    most misleading form, a bundle that looks complete."""
    _, fail_for, tmp_path = batch_env
    fail_for.add("IT_Services")
    jobs = config_loader(config=CONFIG)
    batch_module.execute_batch(_batch_run(jobs), jobs, SidebarFilterRequest())

    archives = list((tmp_path / "archive").glob("*.zip"))
    assert not any("IT_Services" in p.name for p in archives), "no ZIP for an empty failure"

    construction = next(p for p in archives if "Construction" in p.name)
    with zipfile.ZipFile(construction) as zf:
        names = zf.namelist()
    assert names and all(n.startswith("Construction/") for n in names)


def test_a_batch_where_everything_failed_is_a_failed_batch(batch_env):
    _, fail_for, _ = batch_env
    fail_for.update({"IT_Services", "Construction"})
    jobs = config_loader(config=CONFIG)
    batch_id = _batch_run(jobs)

    batch_module.execute_batch(batch_id, jobs, SidebarFilterRequest())

    assert run_manager.get_run(batch_id)["status"] == "failed"


def test_the_batch_reports_each_niche_step_by_step(batch_env, caplog):
    caplog.set_level(logging.INFO)
    jobs = config_loader(config=CONFIG)
    batch_module.execute_batch(_batch_run(jobs), jobs, SidebarFilterRequest())

    log = "\n".join(r.getMessage() for r in caplog.records)
    assert "[NICHE 1/2] START IT_Services" in log
    assert "[STATE RESET]" in log
    assert "[NICHE 1/2] COMPLETED IT_Services" in log
    assert "[ZIP]" in log
    assert "[NICHE 2/2] START Construction" in log


def test_each_niche_run_carries_its_own_batch_root(batch_env):
    """`batch_root` is what routes packaging away from the day-session archiver,
    which is the field that keeps a batch's ZIP from being the whole day."""
    jobs = config_loader(config=CONFIG)
    batch_id = _batch_run(jobs)
    result = batch_module.execute_batch(batch_id, jobs, SidebarFilterRequest())

    for outcome in result["niches"]:
        run = run_manager.get_run(outcome["run_id"])
        assert run["batch_root"], "a batch niche run must be marked as one"
        assert not run.get("session_root"), "and must not join the day's session"


# =============================================================================
# The spreadsheet: this run's rows, not the niche's history
# =============================================================================


def test_the_niche_sheet_is_regenerated_from_this_run_alone(monkeypatch, tmp_path):
    """`exports._refresh_niche_excel` merges every run of a niche in the session
    on purpose — a re-run adds to that niche's bundle. In a batch that is the
    leak, so the batch's own refresh is scoped to one run id."""
    folder = tmp_path / "IT-Services"
    folder.mkdir()
    sheet = folder / "IT-Services_Bids.xlsx"
    sheet.write_text("from the scraper")
    asked: list[str] = []

    def fake_generate(run_id, out_path):
        asked.append(run_id)
        Path(out_path).write_text("db rows")
        return 4

    monkeypatch.setattr(batch_module.export, "generate_excel", fake_generate)
    run = run_manager.create_run("bidnet", folder, {
        "niche_folder": str(folder), "niche_label": "IT Services", "niche_slug": "IT-Services",
    })
    batch_module.refresh_niche_excel(run["run_id"], run)

    assert asked == [run["run_id"]], "only this run's rows may reach the sheet"
    assert sheet.read_text() == "db rows"


def test_an_empty_database_does_not_wipe_the_scrapers_sheet(monkeypatch, tmp_path, caplog):
    """A run whose DB save failed has its bids only in the sheet the scraper
    wrote from memory. Regenerating over it would lose the run's output at the
    last step."""
    caplog.set_level(logging.WARNING)
    folder = tmp_path / "IT-Services"
    folder.mkdir()
    sheet = folder / "IT-Services_Bids.xlsx"
    sheet.write_text("the only copy of this run's bids")

    monkeypatch.setattr(
        batch_module.export, "generate_excel",
        lambda run_id, out_path: (Path(out_path).write_text(""), 0)[1],
    )
    run = run_manager.create_run("bidnet", folder, {
        "niche_folder": str(folder), "niche_label": "IT Services", "niche_slug": "IT-Services",
    })
    batch_module.refresh_niche_excel(run["run_id"], run)

    assert sheet.read_text() == "the only copy of this run's bids"
    assert any("keeping the sheet" in r.getMessage() for r in caplog.records)
