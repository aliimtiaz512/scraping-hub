"""The shape of an MFMP run's output, and the ZIP it becomes.

Both MFMP flows — the niche/keyword search and the ad-status sweep — build the
same tree inside the run's workspace, so a downloaded archive looks the same
whichever produced it:

    MyFlorida_Export/                       <- the ZIP's single root folder
    ├── MyFlorida_Bids_Summary.xlsx         <- every bid, one row each
    └── Bids_Data/
        ├── DMS-21-22-001/                  <- one folder per bid, named by ad number
        │   ├── Solicitation.pdf
        │   └── Specs.docx
        └── RFP-2026-114/
            └── Attachment.pdf

Three things this layout is for, none of which the old flat one gave:

* **The summary is the index.** One sheet at the root carrying every bid's
  metadata *and* the folder its documents are in, so a reviewer reads the sheet
  and opens the folder named in the row rather than guessing which of forty
  directories is which.
* **A bid's documents are only ever in one place.** The folder is named for the
  ad number alone — the stable id the portal, the sheet and the reviewer all
  use. Titles are long, get truncated differently in different places, and two
  ads can share one; ad numbers do neither.
* **The root folder is inside the workspace, not the workspace itself**, so the
  ZIP unpacks to a single `MyFlorida_Export/` rather than scattering its
  contents into whatever directory it was opened in.

`_downloads` (Chrome's in-flight staging) and `_exports` (the raw per-search
workbooks the summary is stitched from) stay *outside* this root, which is what
keeps them out of the archive — see `core.exports._add_tree`.
"""

from __future__ import annotations

from pathlib import Path

from app.core.filenames import sanitize_filename

EXPORT_DIRNAME = "MyFlorida_Export"
BIDS_DIRNAME = "Bids_Data"
SUMMARY_FILENAME = "MyFlorida_Bids_Summary.xlsx"


def export_root(run_dir: Path, create: bool = True) -> Path:
    """The run's `MyFlorida_Export/` — the folder the ZIP is built from."""
    root = Path(run_dir) / EXPORT_DIRNAME
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def bids_data(run_dir: Path, create: bool = False) -> Path:
    """`MyFlorida_Export/Bids_Data/`.

    Not created by default: `bid_folder` makes each bid's folder with
    `parents=True`, so this appears exactly when a bid lands in it and a run
    that found nothing leaves no empty directory behind.
    """
    folder = export_root(run_dir) / BIDS_DIRNAME
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def bid_dirname(ad_number: str, title: str = "") -> str:
    """The folder name for one bid — its ad number, filename-safe.

    `title` is accepted and deliberately unused for the name itself: it is in
    the summary sheet, where it is searchable and untruncated. Folder names are
    an addressing scheme, and the ad number is the only part of a bid that is
    unique, short, stable and quotable back at the portal. It is still taken as
    an argument so a caller with only a bid dict can pass both and the fallback
    below can use the title when the ad number is missing entirely.
    """
    number = sanitize_filename((ad_number or "").strip(), max_length=80)
    if number:
        return number
    fallback = sanitize_filename((title or "").strip(), max_length=60).strip(" ._")
    return fallback or "untitled"


def bid_folder(run_dir: Path, ad_number: str, title: str = "", create: bool = True) -> Path:
    """This bid's own folder under `Bids_Data/`, created on first use."""
    folder = bids_data(run_dir) / bid_dirname(ad_number, title)
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def summary_path(run_dir: Path) -> Path:
    """`MyFlorida_Export/MyFlorida_Bids_Summary.xlsx`."""
    return export_root(run_dir) / SUMMARY_FILENAME


def folder_reference(ad_number: str, title: str = "") -> str:
    """What the summary sheet's Folder column holds — the path *inside* the
    archive, so a row points at a real location in the unpacked ZIP rather than
    at an absolute path on a server the reader has no access to."""
    return f"{BIDS_DIRNAME}/{bid_dirname(ad_number, title)}"
