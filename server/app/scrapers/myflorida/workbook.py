"""Merge the per-search MFMP Excel exports into the run's master summary sheet.

A keyword run exports once per keyword that returned rows (empty passes export
nothing — see the scraper); a sweep exports once per results page. This stitches
those exports into a single `MyFlorida_Bids_Summary.xlsx` at the root of the
run's `MyFlorida_Export/` folder, de-duplicated by ad number, and appends three
columns that tie each row back to its run context and its documents:

    Niche           the search niche this run covered.
    Matched Keyword the keyword(s) whose title search surfaced the ad (comma-
                    joined when several matched); blank for commodity-code runs.
    Folder          where this ad's documents are *inside the archive*, e.g.
                    `Bids_Data/DMS-21-22-001` — a path the reader can actually
                    follow in the unpacked ZIP.

Every column the portal's own export carries is kept, in the order the portal
emits them (Title, Number, Agency, Ad Type, dates, and the rest), so nothing is
lost in translation and no mapping has to be maintained for the portal to change
under. **No row is dropped**: this sheet is the complete record of what the
search returned, and deciding what is worth pursuing is the reviewer's job.

Header detection reuses ingest.parse_excel / map_row so the "which column is the
ad number" logic lives in exactly one place.
"""

import logging
from pathlib import Path

from app.core import excel_style
from app.scrapers.myflorida import storage
from app.scrapers.myflorida.ingest import map_row, parse_excel

logger = logging.getLogger(__name__)

EXTRA_COLUMNS = ("Niche", "Matched Keyword", "Folder")

# The summary sheet built when the portal's own exports are unusable or absent.
# Keyed to what the scraper reads off the detail page, in the order a reviewer
# scans: what it is, who wants it, when it closes, and where the files are.
RECORD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("title", "Title"),
    ("ad_number", "Ad Number"),
    ("agency", "Agency"),
    ("ad_type", "Ad Type"),
    ("status", "Status"),
    ("ad_date", "Ad Date"),
    ("open_date", "Open Date"),
    ("close_date", "Closing Date"),
    ("document_count", "Documents"),
    ("folder", "Folder"),
    ("detail_url", "Detail URL"),
    ("description", "Description"),
)


def build_summary_at(records: list[dict], out_path: Path) -> int:
    """Write the captured bids to `out_path` as the summary sheet. Returns rows.

    Every record is written; nothing is filtered, ranked or scored on the way in.
    Used both for the copy inside the archive and for rebuilding that same sheet
    from the database later, so a download months on matches what shipped.
    """
    workbook, sheet = excel_style.new_workbook("Bids")
    rows = (
        [
            len(record.get("documents") or []) if key == "document_count"
            else record.get(key)
            for key, _ in RECORD_COLUMNS
        ]
        for record in records
    )
    count = excel_style.write_table(sheet, [header for _, header in RECORD_COLUMNS], rows)
    workbook.save(str(out_path))
    logger.info("wrote %d captured bid(s) to %s", count, Path(out_path).name)
    return count


def build_from_records(records: list[dict], run_dir: Path) -> Path:
    """The summary sheet in its place at the root of the run's export folder.

    The fallback for `merge_exports` — used when the portal's Export button gave
    nothing usable, and the reason a run always ships an index even then.
    """
    target = storage.summary_path(run_dir)
    build_summary_at(records, target)
    return target


def merge_exports(
    exports: list[Path],
    run_dir: Path,
    niche: str,
    keyword_by_ad: dict[str, str],
    folder_by_ad: dict[str, str],
) -> Path:
    """Merge `exports` into the run's summary sheet and return its path.

    Rows are de-duplicated by ad number (first export to carry an ad wins). Rows
    without a detectable ad number are all kept — there's no key to merge them on.
    `keyword_by_ad` / `folder_by_ad` are keyed by the ad number as read from the
    results table; a miss just leaves the added cell blank.
    """
    ordered_keys: list[str] = []
    raw_by_key: dict[str, dict] = {}
    ad_by_key: dict[str, str] = {}
    headers: list[str] = []
    seen_headers: set[str] = set()
    noad = 0

    for path in exports:
        for raw in parse_excel(path):
            for header in raw:
                if header not in seen_headers:
                    seen_headers.add(header)
                    headers.append(header)
            ad = map_row(raw).get("ad_number") or ""
            if ad:
                key = f"ad:{ad}"
                if key in raw_by_key:
                    continue  # duplicate ad across keywords — keep the first
            else:
                key = f"noad:{noad}"
                noad += 1
            raw_by_key[key] = raw
            ad_by_key[key] = ad
            ordered_keys.append(key)

    workbook, sheet = excel_style.new_workbook("Bids")
    excel_style.write_table(
        sheet,
        [*headers, *EXTRA_COLUMNS],
        (
            [raw_by_key[key].get(header) for header in headers]
            + [niche, keyword_by_ad.get(ad_by_key[key], ""), folder_by_ad.get(ad_by_key[key], "")]
            for key in ordered_keys
        ),
    )

    target = storage.summary_path(run_dir)
    workbook.save(str(target))
    logger.info("merged %d export(s) into %s (%d rows)", len(exports), target.name, len(ordered_keys))
    return target
