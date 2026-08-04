"""Where a BidNet run's files live, and how a day's niches are bundled.

A run is one niche, but a working day is usually several. The layout keeps them
together instead of shipping one ZIP per niche:

    BidNet_Exports_2026-08-04/          <- the session root, one per day
    ├── IT Services/                    <- one folder per niche actually run
    │   ├── IT Services_Bids.xlsx
    │   └── documents/
    │       ├── REF-101 - Network Refresh/
    │       │   └── bid_101_spec.pdf
    │       └── REF-102 - Helpdesk/
    │           └── bid_102_rfp.pdf
    └── Construction/
        ├── Construction_Bids.xlsx
        └── documents/
            └── REF-201 - Bridge Deck/
                └── bid_201_drawing.pdf

Only niches that actually ran get a folder — the tree is built as runs happen,
never pre-created for the whole catalog.

Two consequences shape the rest of this module:

* **The session root outlives the run.** Every other portal deletes its
  workspace once its ZIP is written; doing that here would throw away the
  morning's niches when the afternoon's run finished. The root is kept until
  the retention window passes (`prune_old_sessions`), so each new run adds a
  folder beside the existing ones rather than replacing them.
* **The ZIP is the whole root, rebuilt per run.** Every run of a given day
  points at the same archive, so downloading from any of them gets every niche
  finished up to that moment. Rebuilt through a temp file and an atomic replace
  so a download in flight never reads a half-written ZIP.

Documents stay in per-bid subfolders under `documents/`. Two solicitations in
one niche routinely ship an "Addendum 1.pdf" apiece, and a flat folder would
land the second on top of the first.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import date, datetime
from pathlib import Path

from app.config import settings
from app.core.filenames import sanitize_filename

logger = logging.getLogger(__name__)

# The session root's name. Dated, so a day's niches collect in one place and a
# new day starts a new bundle without anyone having to clear anything out.
SESSION_PREFIX = "BidNet_Exports"
_SESSION_RE = re.compile(rf"^{re.escape(SESSION_PREFIX)}_(\d{{4}}-\d{{2}}-\d{{2}})$")

# The per-niche documents folder, and the suffix on a niche's spreadsheet.
DOCUMENTS_DIRNAME = "documents"
EXCEL_SUFFIX = "_Bids.xlsx"

# How long a session root is kept in the workspace after its day. It has to
# outlive the day itself (a run finishing at 23:59 must still bundle it), and a
# couple of days of slack means a late download still finds its files.
SESSION_RETENTION_DAYS = 3

# Serialises the rebuild of a session's ZIP: two runs of different niches can
# finish at the same moment and would otherwise write the same archive at once.
zip_lock = threading.Lock()


def session_name(when: date | None = None) -> str:
    """The session root folder name for a given day."""
    return f"{SESSION_PREFIX}_{(when or date.today()).isoformat()}"


def session_root(when: date | None = None) -> Path:
    """The session root for a day, created if it does not exist yet."""
    root = settings.work_root / session_name(when)
    root.mkdir(parents=True, exist_ok=True)
    return root


def niche_dirname(niche_label: str, niche_key: str = "", slug: str | None = None) -> str:
    """Folder name for a niche — its slug when it has one, else its label."""
    return sanitize_filename(slug or niche_label or niche_key or "Niche", max_length=80)


def niche_folder(root: Path, niche_label: str, niche_key: str = "", slug: str | None = None) -> Path:
    """This niche's folder inside `root`, created on first use.

    Re-running a niche the same day reuses the existing folder rather than
    making a second one: the day's bundle should hold one folder per niche, and
    the rerun's documents and refreshed sheet belong with the earlier ones.
    """
    folder = root / niche_dirname(niche_label, niche_key, slug)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def documents_folder(niche_dir: Path, create: bool = False) -> Path:
    """The niche's `documents/` folder.

    Not created by default: the download path makes each bid's subfolder with
    `parents=True`, so `documents/` appears exactly when something lands in it.
    A run that finds no bids then leaves no empty folder behind to be tidied.
    """
    folder = niche_dir / DOCUMENTS_DIRNAME
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def excel_filename(niche_label: str, niche_key: str = "", slug: str | None = None) -> str:
    """`<Niche>_Bids.xlsx` — the niche's spreadsheet, named after the niche."""
    return niche_dirname(niche_label, niche_key, slug) + EXCEL_SUFFIX


def excel_path(niche_dir: Path, niche_label: str, niche_key: str = "", slug: str | None = None) -> Path:
    return niche_dir / excel_filename(niche_label, niche_key, slug)


def zip_name(root: Path) -> str:
    """The archive name for a session root — the root's own name."""
    return sanitize_filename(root.name, max_length=150) + ".zip"


def is_session_root(path: Path) -> bool:
    return bool(_SESSION_RE.match(path.name))


def session_date(path: Path) -> date | None:
    match = _SESSION_RE.match(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def niche_dirs(root: Path) -> list[Path]:
    """Every niche folder currently in a session root, in name order."""
    if not root.is_dir():
        return []
    return sorted(
        (p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")),
        key=lambda p: p.name.lower(),
    )


def prune_old_sessions(keep_days: int = SESSION_RETENTION_DAYS, today: date | None = None) -> list[str]:
    """Delete session roots older than the retention window.

    This is what keeps "nothing is left on local disk" true now that a session
    root is no longer deleted the moment its ZIP is written. Only folders whose
    name matches the session pattern are ever considered, and only inside the
    temp workspace — an unrecognised folder is left alone.
    """
    import shutil

    root = settings.work_root
    if not root.is_dir():
        return []
    cutoff = (today or date.today())
    removed: list[str] = []
    for path in root.iterdir():
        if not path.is_dir():
            continue
        when = session_date(path)
        if when is None:
            continue
        if (cutoff - when).days > keep_days:
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path.name)
    if removed:
        logger.info("pruned %d expired BidNet session folder(s): %s", len(removed), ", ".join(removed))
    return removed
