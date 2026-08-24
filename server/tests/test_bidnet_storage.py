"""BidNet session-storage layout and unified ZIP packaging.

No browser, no portal, no DB: the layout is built by hand the way a run builds
it, and the packaging is exercised against a real temp workspace. What these
pin down is the shape of the deliverable —

    BidNet_Exports_<date>/<Niche>/<Niche>_Bids.xlsx

— one file per niche, since attachment downloading is retired — and the rule
that matters most: a run must bundle *every* niche done that session, not just
its own, and must never destroy a sibling niche's files.

    server/.venv/bin/python server/tests/test_bidnet_storage.py
"""

import os
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings  # noqa: E402
from app.core import exports  # noqa: E402
from app.scrapers.bidnet import storage  # noqa: E402


class _Workspace:
    """Points settings.work_root / archive_root at temp dirs for one test."""

    def __enter__(self):
        self._tmp = TemporaryDirectory()
        base = Path(self._tmp.name)
        self.work = base / "work"
        self.archive = base / "archive"
        self.work.mkdir()
        self.archive.mkdir()
        cls = type(settings)
        self._saved = (cls.work_root, cls.archive_root)
        cls.work_root = property(lambda s, p=self.work: p)
        cls.archive_root = property(lambda s, p=self.archive: p)
        return self

    def __exit__(self, *exc):
        cls = type(settings)
        cls.work_root, cls.archive_root = self._saved
        self._tmp.cleanup()
        return False


def _make_niche(root: Path, label: str) -> Path:
    """Build a niche folder the way a run does — which is now one sheet and
    nothing else: no documents are downloaded, so no per-bid folder exists."""
    folder = storage.niche_folder(root, label)
    storage.excel_path(folder, label).write_bytes(b"xlsx-" + label.encode())
    return folder


# -- layout -----------------------------------------------------------------


def test_session_root_is_dated_and_shared():
    with _Workspace() as ws:
        a = storage.session_root()
        b = storage.session_root()
        assert a == b, "two runs on the same day must share one root"
        assert a.parent == ws.work
        assert a.name == f"BidNet_Exports_{date.today().isoformat()}"


def test_only_executed_niches_get_a_folder():
    """5 niches exist in the catalog; running 2 must leave exactly 2 folders."""
    with _Workspace():
        root = storage.session_root()
        _make_niche(root, "IT Services")
        _make_niche(root, "Construction")
        names = [p.name for p in storage.niche_dirs(root)]
        assert names == ["Construction", "IT Services"], names


def test_niche_folder_holds_its_sheet_and_nothing_else():
    """One niche, one file. The `documents/` subtree the scraper used to fill
    is gone with the downloads that filled it."""
    with _Workspace():
        root = storage.session_root()
        folder = _make_niche(root, "IT Services")
        assert [p.name for p in folder.iterdir()] == ["IT Services_Bids.xlsx"]


def test_rerunning_a_niche_reuses_its_folder():
    """A second run of the same niche must not create a parallel folder."""
    with _Workspace():
        root = storage.session_root()
        first = storage.niche_folder(root, "IT Services")
        second = storage.niche_folder(root, "IT Services")
        assert first == second
        assert len(storage.niche_dirs(root)) == 1


def test_a_new_niche_folder_starts_empty():
    with _Workspace():
        root = storage.session_root()
        folder = storage.niche_folder(root, "Empty Niche")
        assert list(folder.iterdir()) == []


# -- packaging --------------------------------------------------------------


def _run(root: Path, niche_label: str, run_id: str) -> dict:
    return {
        "run_id": run_id,
        "scraper": "bidnet",
        "session_root": str(root),
        "niche_folder": str(root / storage.niche_dirname(niche_label)),
        "folder": str(root / storage.niche_dirname(niche_label)),
        "niche": niche_label.lower().replace(" ", "_"),
        "niche_label": niche_label,
    }


def test_zip_bundles_every_niche_in_the_session():
    """The whole point: a run's download carries the other niches too."""
    with _Workspace():
        root = storage.session_root()
        _make_niche(root, "IT Services")
        _make_niche(root, "Construction")

        out = Path(root.parent) / "bundle.zip"
        name = exports.build_zip(_run(root, "IT Services", "r1"), out)
        assert name == f"{root.name}.zip", name

        with zipfile.ZipFile(out) as zf:
            names = sorted(zf.namelist())
        assert f"{root.name}/IT Services/IT Services_Bids.xlsx" in names, names
        assert f"{root.name}/Construction/Construction_Bids.xlsx" in names, names


def test_a_later_niche_does_not_overwrite_an_earlier_one():
    """Two runs, two niches, one root — the first niche's sheet must survive."""
    with _Workspace():
        root = storage.session_root()
        _make_niche(root, "IT Services")
        first = (root / "IT Services" / "IT Services_Bids.xlsx").read_bytes()

        # a second run, a different niche
        _make_niche(root, "Construction")

        still_there = root / "IT Services" / "IT Services_Bids.xlsx"
        assert still_there.is_file(), "the earlier niche's sheet was destroyed"
        assert still_there.read_bytes() == first


def test_zip_excludes_internal_and_diagnostic_files():
    with _Workspace():
        root = storage.session_root()
        folder = _make_niche(root, "IT Services")
        (folder / "_downloads").mkdir()
        (folder / "_downloads" / "partial.crdownload").write_bytes(b"x")
        (folder / "error_login_page.png").write_bytes(b"png")

        out = Path(root.parent) / "bundle.zip"
        exports.build_zip(_run(root, "IT Services", "r1"), out)
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        assert not any("_downloads" in n for n in names), names
        assert not any(n.endswith("error_login_page.png") for n in names), names
        assert any(n.endswith("IT Services_Bids.xlsx") for n in names), names


def test_zip_of_a_session_with_one_niche_still_nests_under_the_root():
    with _Workspace():
        root = storage.session_root()
        _make_niche(root, "IT Services")
        out = Path(root.parent) / "bundle.zip"
        exports.build_zip(_run(root, "IT Services", "r1"), out)
        with zipfile.ZipFile(out) as zf:
            assert all(n.startswith(f"{root.name}/") for n in zf.namelist()), zf.namelist()


# -- archive_run integration ------------------------------------------------


def _register(root: Path, niche_label: str) -> dict:
    """Register a real run in run_manager, shaped as the router shapes it."""
    from app.core import run_manager

    folder = storage.niche_folder(root, niche_label)
    return run_manager.create_run(
        "bidnet",
        folder,
        {
            "status": "completed",
            "niche": niche_label.lower().replace(" ", "_"),
            "niche_label": niche_label,
            "niche_slug": None,
            "session_root": str(root),
            "niche_folder": str(folder),
        },
    )


def test_archive_run_bundles_all_niches_and_keeps_the_workspace():
    """The behaviour every other portal does NOT have: the root survives.

    Deleting it (as `_cleanup_workspace` does elsewhere) would throw away the
    niches already finished that day.
    """
    from app.core import run_manager

    with _Workspace() as ws:
        root = storage.session_root()
        run_a = _register(root, "IT Services")
        _make_niche(root, "IT Services")
        run_b = _register(root, "Construction")
        _make_niche(root, "Construction")

        archive = exports.archive_run(run_b["run_id"])
        assert archive is not None, "packaging failed"
        assert Path(archive).is_file()
        assert Path(archive).name == f"{root.name}.zip"

        # the session root is still on disk, with both niches intact
        assert root.is_dir(), "the session root was deleted — earlier niches lost"
        assert (root / "IT Services" / "IT Services_Bids.xlsx").is_file()

        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
        assert any(n.endswith("IT Services/IT Services_Bids.xlsx") for n in names), names
        assert any(n.endswith("Construction/Construction_Bids.xlsx") for n in names), names

        # both runs of the session point at the same bundle, so downloading
        # from the earlier niche also gets the later one
        for run_id in (run_a["run_id"], run_b["run_id"]):
            assert run_manager.get_run(run_id)["zip_path"] == archive, run_id
        assert ws.archive in Path(archive).parents


def test_archive_run_is_rebuilt_as_each_niche_finishes():
    with _Workspace():
        root = storage.session_root()
        run_a = _register(root, "IT Services")
        _make_niche(root, "IT Services")
        first = exports.archive_run(run_a["run_id"])
        with zipfile.ZipFile(first) as zf:
            assert not any("Construction" in n for n in zf.namelist())

        run_b = _register(root, "Construction")
        _make_niche(root, "Construction")
        second = exports.archive_run(run_b["run_id"])

        assert second == first, "the session's ZIP should be one archive, refreshed"
        with zipfile.ZipFile(second) as zf:
            names = zf.namelist()
        assert any("IT Services" in n for n in names), names
        assert any("Construction" in n for n in names), names


def test_an_empty_db_does_not_wipe_the_scrapers_sheet():
    """A run whose DB save failed has its bids only in the on-disk sheet.

    Regenerating over that path from an empty DB would replace real results
    with a blank workbook at the very last step of the run.
    """
    from app.scrapers.bidnet import export as bidnet_export

    with _Workspace():
        root = storage.session_root()
        run = _register(root, "IT Services")
        _make_niche(root, "IT Services")
        sheet = root / "IT Services" / "IT Services_Bids.xlsx"
        original = sheet.read_bytes()

        def empty_db(run_ids, out_path):
            Path(out_path).write_bytes(b"EMPTY-WORKBOOK")
            return 0

        saved = bidnet_export.generate_excel_for_runs
        bidnet_export.generate_excel_for_runs = empty_db
        try:
            exports.archive_run(run["run_id"])
        finally:
            bidnet_export.generate_excel_for_runs = saved

        assert sheet.read_bytes() == original, "the scraper's sheet was overwritten"
        leftovers = [p.name for p in sheet.parent.iterdir() if p.name.endswith(".tmp")]
        assert not leftovers, leftovers


def test_a_populated_db_refreshes_the_sheet():
    from app.scrapers.bidnet import export as bidnet_export

    with _Workspace():
        root = storage.session_root()
        run = _register(root, "IT Services")
        _make_niche(root, "IT Services")
        sheet = root / "IT Services" / "IT Services_Bids.xlsx"

        def populated(run_ids, out_path):
            Path(out_path).write_bytes(b"FRESH-FROM-DB")
            return 7

        saved = bidnet_export.generate_excel_for_runs
        bidnet_export.generate_excel_for_runs = populated
        try:
            exports.archive_run(run["run_id"])
        finally:
            bidnet_export.generate_excel_for_runs = saved

        assert sheet.read_bytes() == b"FRESH-FROM-DB"
        assert not [p for p in sheet.parent.iterdir() if p.name.endswith(".tmp")]


def test_archive_run_survives_a_missing_session_root():
    with _Workspace():
        root = storage.session_root()
        run = _register(root, "IT Services")
        import shutil

        shutil.rmtree(root)
        assert exports.archive_run(run["run_id"]) is None


# -- retention --------------------------------------------------------------


def test_prune_removes_expired_sessions_only():
    with _Workspace() as ws:
        today = date(2026, 8, 4)
        keep_today = storage.session_root(today)
        keep_recent = storage.session_root(today - timedelta(days=2))
        expired = storage.session_root(today - timedelta(days=9))
        unrelated = ws.work / "Myflorida run (2026-08-01)"
        unrelated.mkdir()

        removed = storage.prune_old_sessions(keep_days=3, today=today)

        assert expired.name in removed, removed
        assert keep_today.is_dir()
        assert keep_recent.is_dir()
        assert not expired.exists()
        assert unrelated.is_dir(), "a non-session folder must never be pruned"


def test_prune_ignores_unparseable_names():
    with _Workspace() as ws:
        odd = ws.work / "BidNet_Exports_not-a-date"
        odd.mkdir()
        assert storage.prune_old_sessions(keep_days=0, today=date(2026, 8, 4)) == []
        assert odd.is_dir()


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:
            print(f"FAIL  {name}: {exc}")
            failures += 1
        except Exception as exc:  # noqa: BLE001 — report, don't abort the suite
            print(f"ERROR {name}: {exc.__class__.__name__}: {exc}")
            failures += 1
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
