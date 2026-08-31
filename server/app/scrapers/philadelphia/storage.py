"""Where a PHLContracts run writes its one deliverable.

    CityOfPhiladelphia_Export/
    └── Philadelphia_Bids_Summary.xlsx      <- every open bid, one row each

That is the whole layout. It used to be a tree — the summary at the root and a
`Bids_Data/<bid number>/` folder per bid holding that bid's attachments and a
`bid_items_details.txt` — packaged as a ZIP. The client asked for the bids and
not their paperwork, so nothing is downloaded now and `philadelphia` sits in
`exports.EXCEL_ONLY_PORTALS`: the run ships the bare `.xlsx`, because a ZIP
around a single file is a folder to unpack for nothing.

The export folder is kept even though it holds one file. `archive_run` reads the
run's `excel_path` and the workspace is deleted afterwards either way, so the
folder costs nothing — and keeping it means the sheet has one home across both
the completed path and the stopped one (`flush_partial`), rather than two places
that can drift apart.

What the tree used to carry now lives in the sheet: the header table became
columns in an earlier change, and the line items became the `Line Item Details`
column when the folders went. Nothing that reached a reader before is missing;
the attachments are counted in `Total Document Count` and reachable through
`Detail URL`.
"""

from __future__ import annotations

from pathlib import Path

EXPORT_DIRNAME = "CityOfPhiladelphia_Export"
SUMMARY_FILENAME = "Philadelphia_Bids_Summary.xlsx"


def export_root(run_dir: Path, create: bool = True) -> Path:
    """The run's `CityOfPhiladelphia_Export/` — where the summary sheet goes."""
    root = Path(run_dir) / EXPORT_DIRNAME
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def summary_path(run_dir: Path) -> Path:
    """`CityOfPhiladelphia_Export/Philadelphia_Bids_Summary.xlsx`."""
    return export_root(run_dir) / SUMMARY_FILENAME
