"""Merge the per-keyword MFMP Excel exports into one workbook per run.

A keyword run exports once per keyword that returned rows (empty passes export
nothing — see the scraper). This stitches those exports into a single
`<Niche>_bids.xlsx`, de-duplicated by ad number, and appends three columns that
tie each row back to its run context and its documents on disk:

    Niche           the search niche this run covered.
    Matched Keyword the keyword(s) whose title search surfaced the ad (comma-
                    joined when several matched); blank for commodity-code runs.
    Folder          the ad's document folder name under the run directory.

Header detection reuses ingest.parse_excel / map_row so the "which column is the
ad number" logic lives in exactly one place.
"""

import logging
from pathlib import Path

from openpyxl import Workbook

from app.core.filenames import sanitize_filename
from app.scrapers.myflorida.ingest import map_row, parse_excel

logger = logging.getLogger(__name__)

EXTRA_COLUMNS = ("Niche", "Matched Keyword", "Folder")


def merge_exports(
    exports: list[Path],
    run_dir: Path,
    niche: str,
    keyword_by_ad: dict[str, str],
    folder_by_ad: dict[str, str],
) -> Path:
    """Merge `exports` into one workbook under `run_dir` and return its path.

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

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Bids"
    sheet.append([*headers, *EXTRA_COLUMNS])
    for key in ordered_keys:
        raw = raw_by_key[key]
        ad = ad_by_key[key]
        row = [raw.get(header) for header in headers]
        row += [niche, keyword_by_ad.get(ad, ""), folder_by_ad.get(ad, "")]
        sheet.append(row)

    target = run_dir / f"{sanitize_filename(niche)}_bids.xlsx"
    workbook.save(str(target))
    logger.info("merged %d export(s) into %s (%d rows)", len(exports), target.name, len(ordered_keys))
    return target
