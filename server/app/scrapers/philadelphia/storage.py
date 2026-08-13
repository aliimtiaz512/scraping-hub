"""The shape of a PHLContracts run's output, and the ZIP it becomes.

    CityOfPhiladelphia_Export/              <- the ZIP's single root folder
    ├── Philadelphia_Bids_Summary.xlsx      <- every open bid, one row each
    └── Bids_Data/
        ├── B2727750/                       <- one folder per bid, named by bid #
        │   ├── bid_items_details.txt       <- the line items, in plain text
        │   ├── MP_Terms_and_Conditions_B2727750.pdf
        │   └── Consent_Authorization_B2727750.pdf
        └── B2727746/
            └── …

The same layout the MyFlorida flow uses, for the same reasons (see
`app/scrapers/myflorida/storage.py`): the summary at the root is the index, each
row names the folder holding that bid's files, and the root folder lives *inside*
the workspace so the archive unpacks to one directory rather than scattering its
contents into whatever directory it was opened in.

**Everything here opens without a developer.** This layout used to carry an
`Extra_Header_Info.json` per bid, which put the detail page's header table in
front of a procurement team in a format their machine offers to open in a code
editor. That table now goes into the summary sheet — three fields as columns of
their own and the rest as one readable cell — and the bid's line items, which
were not captured at all, go beside the documents as `bid_items_details.txt`.
The attachments stay exactly as they were: whole files, unchanged, in the bid's
own folder.
"""

from __future__ import annotations

from pathlib import Path

from app.core.filenames import sanitize_filename

EXPORT_DIRNAME = "CityOfPhiladelphia_Export"
BIDS_DIRNAME = "Bids_Data"
SUMMARY_FILENAME = "Philadelphia_Bids_Summary.xlsx"
ITEMS_FILENAME = "bid_items_details.txt"

#: Files the run writes into a bid's folder itself. They are deliverables, but
#: they are not documents the city published — so they are never counted as
#: attachments, which is what the "Total Document Count" column reports.
#: `Extra_Header_Info.json` is listed because a workspace left over from before
#: it was dropped must not start counting as a document now.
GENERATED_FILENAMES = frozenset({ITEMS_FILENAME, "Extra_Header_Info.json"})


def export_root(run_dir: Path, create: bool = True) -> Path:
    """The run's `CityOfPhiladelphia_Export/` — the folder the ZIP is built from."""
    root = Path(run_dir) / EXPORT_DIRNAME
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def bids_data(run_dir: Path, create: bool = False) -> Path:
    """`CityOfPhiladelphia_Export/Bids_Data/`.

    Not created by default: `bid_folder` makes each bid's folder with
    `parents=True`, so this appears exactly when a bid lands in it and a run
    that found nothing leaves no empty directory behind.
    """
    folder = export_root(run_dir) / BIDS_DIRNAME
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def bid_dirname(bid_number: str) -> str:
    """The folder name for one bid — its bid number, filename-safe.

    The bid number alone: it is unique, short, stable, and the thing a reader
    quotes back at the portal. Descriptions are long, truncate differently in
    different places, and two bids can share one.
    """
    return sanitize_filename((bid_number or "").strip(), max_length=80) or "unnumbered"


def bid_folder(run_dir: Path, bid_number: str, create: bool = True) -> Path:
    """This bid's own folder under `Bids_Data/`, created on first use."""
    folder = bids_data(run_dir) / bid_dirname(bid_number)
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def saved_documents(run_dir: Path, bid_number: str) -> list[str]:
    """The documents actually in a bid's folder, by name.

    The bid's document count, taken from the disk rather than from how many
    links the detail page offered or how many clicks the run made. Those three
    numbers agree on a good run and diverge on exactly the runs worth knowing
    about — a count that comes from anywhere else can report five documents to
    someone who opens the folder and finds three.

    `bid_items_details.txt` is written by the run, not published by the city, so
    it is not counted — see `GENERATED_FILENAMES`.
    """
    folder = bid_folder(run_dir, bid_number, create=False)
    if not folder.is_dir():
        return []
    return sorted(
        p.name for p in folder.iterdir()
        if p.is_file() and p.name not in GENERATED_FILENAMES
    )


def summary_path(run_dir: Path) -> Path:
    """`CityOfPhiladelphia_Export/Philadelphia_Bids_Summary.xlsx`."""
    return export_root(run_dir) / SUMMARY_FILENAME


def items_path(run_dir: Path, bid_number: str) -> Path:
    """Where one bid's `bid_items_details.txt` goes — beside its documents."""
    return bid_folder(run_dir, bid_number) / ITEMS_FILENAME


def folder_reference(bid_number: str) -> str:
    """What the summary's Folder column holds — the path *inside* the archive,
    so a row points at a real location in the unpacked ZIP rather than at an
    absolute path on a server the reader has no access to."""
    return f"{BIDS_DIRNAME}/{bid_dirname(bid_number)}"
