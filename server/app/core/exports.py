"""Result packaging: every run ends as one complete ZIP, nothing on local disk.

While a run is going, its documents land in a scratch workspace folder
(settings.work_root — system temp). On completion `archive_run` packages the
whole run into a single ZIP — the cumulative Excel report (built fresh from the
DB) plus every downloaded bid document, keeping the original niche-wise folder
structure — stores it in settings.archive_root, and deletes the workspace.
That one ZIP is what the Download button serves and what the completion email
attaches/links, and nothing is ever written into data/documents.
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

# Portals whose runs download real document files → their download is a ZIP.
DOC_PORTALS = {"myflorida", "bidnet", "northdakota"}

# Portals whose export module can rebuild the run's Excel from the DB via
# `generate_excel(run_id, path)`. MyFlorida is absent on purpose: its workbook
# is downloaded from the portal itself and merged on disk (run["excel_path"]).
_GENERATOR_PORTALS = {"septa", "wisconsin", "ridemetro", "northdakota", "sam", "unison", "bidnet"}


def _excel_name(run: dict[str, Any]) -> str:
    """Download filename for a regenerated sheet, following each portal's
    existing naming convention (criteria in the name, no timestamps)."""
    scraper = run.get("scraper") or "results"
    search = (run.get("search") or "").strip()
    label = {
        "septa": f"Septa_({search or 'today open quotes'})",
        "wisconsin": f"Wisconsin_({search or 'all current solicitations'})",
        "sam": f"SAM_({search or 'all active solicitations'})",
        "unison": f"Unison_({(run.get('filter_by') or 'all requests').strip()})",
        "ridemetro": f"RideMetro_Bids ({run.get('label') or run['run_id']})",
        "northdakota": f"NorthDakota_({search or 'all public solicitations'})",
        "bidnet": f"Bidnetdirect_({search or 'all solicitations'})",
    }.get(scraper, f"{scraper}_{run['run_id']}")
    return sanitize_filename(label, max_length=150) + ".xlsx"


def excel_bytes(run: dict[str, Any]) -> tuple[bytes, str] | None:
    """The run's Excel as (bytes, filename).

    Regenerated fresh from the DB when the portal supports it; otherwise (or if
    generation fails) read from the run's on-disk workbook — MyFlorida's merged
    export, or the fallback sheet a scraper writes when the DB was down.
    """
    scraper = run.get("scraper")
    if scraper in _GENERATOR_PORTALS:
        try:
            module = importlib.import_module(f"app.scrapers.{scraper}.export")
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "export.xlsx"
                module.generate_excel(run["run_id"], out)
                return out.read_bytes(), _excel_name(run)
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
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if scraper == "bidnet":
            # BidNet's run folder is the shared per-day bucket; only this run's
            # group folders belong in its ZIP. Older runs recorded no groups —
            # for those the day bucket is the closest thing to the run's output.
            groups = [Path(g) for g in run.get("group_folders") or []]
            if not groups and folder.is_dir():
                groups = [folder]
            for group in groups:
                if group.is_dir():
                    _add_tree(zf, group, group.name if group != folder else "")
        elif folder.is_dir():
            _add_tree(zf, folder)

        payload = excel_bytes(run)
        if payload:
            data, name = payload
            # MyFlorida's merged workbook lives inside the run folder and was
            # already added by the walk above — don't duplicate it.
            if name not in zf.namelist():
                zf.writestr(name, data)

    label = folder.name or f"{scraper}_{run['run_id']}"
    return sanitize_filename(label, max_length=150) + ".zip"


def archive_run(run_id: str) -> str | None:
    """Package a finished run into its final ZIP and clean up its workspace.

    The ZIP (cumulative Excel + all bid documents in their niche-wise folders)
    is written to settings.archive_root and recorded on the run as `zip_path`;
    the temp workspace folder is then deleted. Best-effort: on failure the
    workspace is kept so the download endpoint can still package it on demand.
    Returns the archive path, or None if packaging failed.
    """
    from app.core import run_manager

    run = run_manager.get_run(run_id)
    if not run:
        return None
    folder = Path(run.get("folder") or "")
    label = folder.name or f"{run.get('scraper')}_{run_id}"
    # The run_id suffix keeps same-second runs from colliding in the archive.
    out = settings.archive_root / (sanitize_filename(label, max_length=140) + f" [{run_id}].zip")
    try:
        build_zip(run, out)
    except Exception:  # noqa: BLE001 — packaging must never fail the run
        logger.exception("[run %s] could not build archive ZIP", run_id)
        out.unlink(missing_ok=True)
        return None

    run_manager.update_run(run_id, zip_path=str(out), zip_name=out.name)
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
