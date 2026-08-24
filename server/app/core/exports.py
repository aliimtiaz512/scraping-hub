"""Result packaging: every run ends as one archive, nothing left on local disk.

While a run is going, its output lands in a scratch workspace folder
(settings.work_root — system temp). On completion `archive_run` packages the
whole run into a single ZIP — the cumulative Excel report (built fresh from the
DB) plus any downloaded bid documents, keeping the original niche-wise folder
structure — stores it in settings.archive_root, and deletes the workspace.
That one ZIP is what the Download button serves and what the completion email
attaches/links, and nothing is ever written into data/documents.

Some runs deliver a bare .xlsx instead, because a ZIP around a single
spreadsheet is a folder to unpack for nothing. That is usually a property of the
portal (EXCEL_ONLY_PORTALS) and sometimes of the individual run — BidNet ships
ZIPs for its niche runs and one sheet for its member-agency sweep. `is_excel_only`
is the single question both the packaging step and the download endpoint ask.
"""

import importlib
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.filenames import sanitize_filename

logger = logging.getLogger(__name__)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

<<<<<<< HEAD
# Portals whose runs download real document files → their download is a ZIP.
DOC_PORTALS = {"myflorida", "myflorida_sweep", "bidnet", "northdakota", "philadelphia"}

# Portals whose run produces nothing but the spreadsheet, so wrapping it in a
# ZIP adds a folder to unpack for no gain. SAM downloads each bid's attachments
# only to extract their text for the evaluator, then deletes them — the sheet is
# the entire deliverable. These runs are archived and delivered as a bare .xlsx.
# SEPTA never downloads anything at all — its Open Quotes grid is metadata only,
# so the merged sheet across a niche's searches is the entire deliverable.
# RideMetro is the same: its Euna Supplier Network sweep only reads each agency's
# opportunities list, and the agency-grouped report is all a run produces.
# Unison joined them once its evaluator went in: it fetches each buy's
# attachments to read them into the decision, then discards them — after the
# verdict the files serve no purpose, so the sheet is the deliverable.
#
# The MyFlorida sweep used to be here, for exactly that reason: it downloaded
# each ad's attachments only to feed the classifier's scope signal and then
# deleted them. It keeps them now (the classifier is gone from that pipeline),
# so its runs deliver the same ZIP the niche flow does — a summary sheet beside
# the documents it indexes.
# EMMA is here for the same reason as SAM: it opens each solicitation's
# documents only to screen their text against the keyword blocklist, then
# deletes them — the spreadsheet of passing bids is the entire deliverable.
EXCEL_ONLY_PORTALS = {"sam", "septa", "ridemetro", "unison", "emma"}


def is_excel_only(run: dict[str, Any]) -> bool:
    """Does this run deliver a bare .xlsx rather than a ZIP?

    Normally a property of the portal (EXCEL_ONLY_PORTALS above), but not
    always: BidNet ships ZIPs for its niche runs and a single consolidated sheet
    for the "all member agency bids" sweep, so that one run says so for itself
    with `excel_only`. Both the packaging step and the download endpoint ask
    here, so the two cannot disagree about what a run produced.
    """
    return bool(run.get("excel_only")) or run.get("scraper") in EXCEL_ONLY_PORTALS

# Portals whose export module can rebuild the run's Excel from the DB via
# `generate_excel(run_id, path)`. MyFlorida is absent on purpose: its workbook
# is downloaded from the portal itself and merged on disk (run["excel_path"]).
_GENERATOR_PORTALS = {
    "septa", "wisconsin", "ridemetro", "northdakota", "sam", "unison", "bidnet",
    "myflorida_sweep", "emma", "philadelphia",
}

# Where a portal's export module lives, when it is not `app.scrapers.<key>`.
# The MyFlorida sweep is a sub-package of the portal it belongs to, so its key
# does not spell its import path.
_EXPORT_MODULES = {"myflorida_sweep": "app.scrapers.myflorida.sweep.export"}


def excel_name(run: dict[str, Any]) -> str:
    """Download filename for a regenerated sheet, following each portal's
    existing naming convention (criteria in the name, no timestamps)."""
    # A run that already knows what its file should be called. Set by flows
    # whose filename is part of the deliverable rather than derived from search
    # criteria — BidNet's member agency sweep, whose sheet is named for the day
    # it swept. Checked first so no later branch can rename it.
    if run.get("download_name"):
        return str(run["download_name"])

    scraper = run.get("scraper") or "results"
    search = (run.get("search") or "").strip()
    # BidNet names its sheet after the niche, matching the copy inside the
    # session ZIP — a bare-sheet fallback download shouldn't arrive under a
    # different name than the one in the bundle.
    if scraper == "bidnet" and run.get("niche_label"):
        from app.scrapers.bidnet import storage

        return storage.excel_filename(
            run["niche_label"], run.get("niche") or "", run.get("niche_slug")
        )
    label = {
        "septa": f"Septa_({search or 'all open quotes'})",
        "wisconsin": f"Wisconsin_({search or 'all current solicitations'})",
        "sam": f"SAM_({search or 'all active solicitations'})",
        "unison": f"Unison_({(run.get('filter_by') or 'all requests').strip()})",
        "ridemetro": f"RideMetro_Bids ({run.get('label') or run['run_id']})",
        "northdakota": f"NorthDakota_({search or 'all public solicitations'})",
        "bidnet": f"Bidnetdirect_({search or 'all solicitations'})",
        "myflorida_sweep": f"MyFlorida_Sweep_({search or 'all statuses'})",
        "emma": f"EMMA_({search or 'all public solicitations'})",
        "philadelphia": f"Philadelphia_({search or 'all open bids'})",
    }.get(scraper, f"{scraper}_{run['run_id']}")
    return sanitize_filename(label, max_length=150) + ".xlsx"


def excel_bytes(run: dict[str, Any]) -> tuple[bytes, str] | None:
    """The run's Excel as (bytes, filename).

    Regenerated fresh from the DB when the portal supports it; otherwise (or if
    generation fails) read from the run's on-disk workbook — MyFlorida's merged
    export, or the fallback sheet a scraper writes when the DB was down.

    A run whose DB save failed is the exception: regenerating it would *succeed*
    and return an empty workbook, silently replacing the fallback sheet that
    holds the only copy of the rows. Generation failing is not the signal to
    watch for here — the DB being empty is — so the flag the scraper sets is
    what decides, and such a run reads straight from disk.
    """
    scraper = run.get("scraper")
    if scraper in _GENERATOR_PORTALS and not run.get("db_save_failed"):
        try:
            module = importlib.import_module(
                _EXPORT_MODULES.get(scraper, f"app.scrapers.{scraper}.export")
            )
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "export.xlsx"
                module.generate_excel(run["run_id"], out)
                return out.read_bytes(), excel_name(run)
        except Exception:  # noqa: BLE001 — fall back to any on-disk copy
            logger.exception("[run %s] on-demand Excel generation failed", run.get("run_id"))

    path = run.get("excel_path")
    if path and Path(path).is_file():
        p = Path(path)
        try:
            return p.read_bytes(), p.name
        except OSError:
            logger.exception("[run %s] could not read excel at %s", run.get("run_id"), path)
    return None


def _add_tree(zf: zipfile.ZipFile, root: Path, arc_prefix: str = "") -> None:
    """Add every result file under `root`, keeping the folder structure."""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        # MyFlorida's raw per-keyword staging — internal, superseded by the merge.
        if "_exports" in rel.parts:
            continue
        # The browser's in-flight download staging dir — internal.
        if "_downloads" in rel.parts:
            continue
        # Failure screenshots are diagnostics, not results.
        if path.name.startswith("error_") and path.suffix == ".png":
            continue
        zf.write(path, str(Path(arc_prefix) / rel) if arc_prefix else str(rel))


def build_zip(run: dict[str, Any], out_path: Path) -> str:
    """Write the run's ZIP (Excel + downloaded documents) to `out_path`.

    Returns the filename the browser should save it as.
    """
    scraper = run.get("scraper")
    folder = Path(run.get("folder") or "")

    # A BidNet run writes into one niche folder of a shared per-day session
    # root, and the deliverable is the whole root — every niche run that day.
    # Zipping `folder` alone would ship a single niche and silently drop the
    # rest of the day's work.
    session_root = Path(run.get("session_root") or "") if scraper == "bidnet" else None
    if session_root and session_root.is_dir():
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            _add_tree(zf, session_root, arc_prefix=session_root.name)
        return sanitize_filename(session_root.name, max_length=150) + ".zip"

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Legacy BidNet runs foldered results per niche+tier group inside a
        # shared per-day bucket, so only this run's groups belonged in its ZIP.
        # Older single-niche runs point `folder` at exactly their own output —
        # the generic walk below covers both.
        if folder.is_dir():
            _add_tree(zf, folder)

        payload = excel_bytes(run)
        if payload:
            data, name = payload
            # A sheet already inside the walked folder must not be added a second
            # time at the ZIP root. Checked on the *path*, not the name: MyFlorida
            # writes its summary to `MyFlorida_Export/MyFlorida_Bids_Summary.xlsx`,
            # whose arcname carries a folder prefix and so never equals the bare
            # download name this payload comes back under.
            on_disk = Path(run.get("excel_path") or "")
            already_added = bool(on_disk.is_file() and folder in on_disk.parents)
            if not already_added and name not in zf.namelist():
                zf.writestr(name, data)

    label = folder.name or f"{scraper}_{run['run_id']}"
    return sanitize_filename(label, max_length=150) + ".zip"


def archive_run(run_id: str) -> str | None:
    """Package a finished run into its final artifact and clean up its workspace.

    Normally that artifact is one ZIP (cumulative Excel + all bid documents in
    their niche-wise folders), written to settings.archive_root and recorded on
    the run as `zip_path`. For EXCEL_ONLY_PORTALS it is the bare .xlsx instead.
    Either way the temp workspace folder is then deleted. Best-effort: on
    failure the workspace is kept so the download endpoint can still package it
    on demand. Returns the archive path, or None if packaging failed.

    BidNet is the exception, and has three of its own paths. A single-niche run
    accumulates into a shared per-day session root, so it packages the whole root
    and keeps it (see `_archive_bidnet`). A run belonging to a **batch** carries
    `batch_root` instead, and packages only its own niche folder — the isolation
    a batch exists for reaches all the way to the archive, or the ZIP would hand
    back the very niches the run was kept apart from (see `batch.archive_niche`).
    The two fields are mutually exclusive, so which one is set selects the path.
    The third is the "all member agency bids" sweep, which belongs to no session
    and no batch and carries `excel_only` — it falls through to the bare-sheet
    path below, because one consolidated spreadsheet is the whole deliverable
    and a ZIP around a single file is a folder to unpack for nothing.
    """
    from app.core import run_manager

    run = run_manager.get_run(run_id)
    if not run:
        return None
    if run.get("scraper") == "bidnet" and run.get("batch_root"):
        from app.scrapers.bidnet import batch as bidnet_batch

        return bidnet_batch.archive_niche(run_id, run)
    if run.get("scraper") == "bidnet" and run.get("session_root"):
        return _archive_bidnet(run_id, run)
    folder = Path(run.get("folder") or "")
    label = folder.name or f"{run.get('scraper')}_{run_id}"
    # The run_id suffix keeps same-second runs from colliding in the archive.
    stem = settings.archive_root / (sanitize_filename(label, max_length=140) + f" [{run_id}]")

    if is_excel_only(run):
        return _archive_excel(run_id, run, folder, stem.with_suffix(".xlsx"))

    out = stem.with_suffix(".zip")
    try:
        build_zip(run, out)
    except Exception:  # noqa: BLE001 — packaging must never fail the run
        logger.exception("[run %s] could not build archive ZIP", run_id)
        out.unlink(missing_ok=True)
        return None

    run_manager.update_run(run_id, zip_path=str(out), zip_name=out.name)
    _cleanup_workspace(run_id, folder)
    return str(out)


def _archive_bidnet(run_id: str, run: dict[str, Any]) -> str | None:
    """Bundle a BidNet session root — every niche run that day — into one ZIP.

    Unlike every other portal, the workspace is **not** deleted afterwards: the
    root is shared, and removing it would throw away the niches already
    finished. Instead each run refreshes its own niche's spreadsheet, rebuilds
    the whole-root ZIP, and points every run of that day at it, so a download
    triggered from any of them carries all niches completed so far. Expired
    session roots are pruned on the way out.
    """
    from app.core import run_manager
    from app.scrapers.bidnet import storage

    root = Path(run["session_root"])
    if not root.is_dir():
        logger.error("[run %s] session root %s is gone — nothing to archive", run_id, root)
        return None

    _refresh_niche_excel(run_id, run)

    out = settings.archive_root / storage.zip_name(root)
    # One writer at a time: two niches can finish together, and both rebuild
    # this same archive. The temp-then-replace also means a download in flight
    # never reads a half-written ZIP.
    with storage.zip_lock:
        tmp = out.with_name(out.name + f".{run_id}.tmp")
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
                _add_tree(zf, root, arc_prefix=root.name)
            tmp.replace(out)
        except Exception:  # noqa: BLE001 — packaging must never fail the run
            logger.exception("[run %s] could not build BidNet session ZIP", run_id)
            tmp.unlink(missing_ok=True)
            return None

    # Every run of this session points at the same archive, so an earlier
    # niche's Download button serves the bundle including the later ones.
    for sibling in run_manager.list_runs("bidnet"):
        if sibling.get("session_root") == str(root):
            run_manager.update_run(sibling["run_id"], zip_path=str(out), zip_name=out.name)

    logger.info(
        "[run %s] packaged %d niche folder(s) from %s into %s",
        run_id, len(storage.niche_dirs(root)), root.name, out.name,
    )
    storage.prune_old_sessions()
    return str(out)


def _refresh_niche_excel(run_id: str, run: dict[str, Any]) -> None:
    """Rewrite this niche's `<Niche>_Bids.xlsx` from the DB.

    Covers every run of the same niche in the same session, so a re-run adds to
    the sheet instead of replacing the earlier run's rows. Best-effort — the
    scraper already wrote a records-based sheet at this exact path, which stands
    if the DB is unreachable.
    """
    from app.core import run_manager
    from app.scrapers.bidnet import export as bidnet_export, storage

    folder = Path(run.get("niche_folder") or run.get("folder") or "")
    if not folder.is_dir():
        return
    run_ids = [
        sibling["run_id"]
        for sibling in run_manager.list_runs("bidnet")
        if sibling.get("session_root") == run.get("session_root")
        and sibling.get("niche") == run.get("niche")
    ]
    if run_id not in run_ids:
        run_ids.append(run_id)

    out = storage.excel_path(
        folder, run.get("niche_label") or "", run.get("niche") or "", run.get("niche_slug")
    )
    # Build beside the real sheet and only swap it in once it is known to hold
    # rows. A run whose DB save failed has its bids *only* in the sheet the
    # scraper wrote from memory — regenerating straight over that path would
    # replace real results with an empty workbook, losing the run's output at
    # the last step.
    staging = out.with_name(out.name + f".{run_id}.tmp")
    try:
        written = bidnet_export.generate_excel_for_runs(run_ids, staging)
        if written == 0 and out.is_file():
            logger.warning(
                "[run %s] the DB returned no rows for this niche — keeping the "
                "sheet the scraper wrote (%s)", run_id, out.name,
            )
            staging.unlink(missing_ok=True)
            return
        staging.replace(out)
        run_manager.update_run(run_id, excel_path=str(out), excel_name=out.name)
        logger.info("[run %s] niche sheet %s holds %d row(s)", run_id, out.name, written)
    except Exception:  # noqa: BLE001 — the scraper's own sheet is already there
        logger.exception("[run %s] could not refresh the niche spreadsheet", run_id)
        staging.unlink(missing_ok=True)


def _archive_excel(run_id: str, run: dict[str, Any], folder: Path, out: Path) -> str | None:
    """Persist an excel-only run's sheet into the archive, unwrapped.

    The sheet is materialised here rather than left to be regenerated on demand
    so it survives the workspace deletion — that matters for a run whose DB save
    failed, where the in-memory records were only ever written to the scratch
    workbook that this replaces.
    """
    from app.core import run_manager

    payload = excel_bytes(run)
    if payload is None:
        logger.error("[run %s] no Excel to archive — keeping the workspace", run_id)
        return None
    data, _ = payload
    try:
        out.write_bytes(data)
    except OSError:
        logger.exception("[run %s] could not write archive Excel", run_id)
        out.unlink(missing_ok=True)
        return None

    # excel_path now points at the archive, so the download/email paths keep
    # working after the workspace is gone. No zip_path is recorded — its absence
    # is what tells the delivery layer this run ships a bare sheet.
    run_manager.update_run(run_id, excel_path=str(out), excel_name=out.name)
    _cleanup_workspace(run_id, folder)
    return str(out)


def _cleanup_workspace(run_id: str, folder: Path) -> None:
    """Delete the run's scratch folder (and any now-empty parents) once its
    contents live in the archive ZIP. Only ever touches paths inside the temp
    workspace — legacy folders under data/documents are never deleted."""
    try:
        resolved = folder.resolve()
        root = settings.work_root
        if root not in resolved.parents:
            return
        shutil.rmtree(resolved, ignore_errors=True)
        # MyFlorida nests date/niche/run — sweep away parents left empty.
        current = resolved.parent
        while current != root and root in current.parents:
            if any(current.iterdir()):
                break
            current.rmdir()
            current = current.parent
    except OSError:  # noqa: BLE001 — tidying is best-effort
        logger.debug("[run %s] workspace cleanup incomplete", run_id, exc_info=True)
