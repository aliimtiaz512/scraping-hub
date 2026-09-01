"""The run's master summary sheet — one row per advertisement, fixed columns.

Every MFMP run, niche or sweep, ships one `MyFlorida_Bids_Summary.xlsx` at the
root of its `MyFlorida_Export/` folder, and this module decides what is in it.

The columns are the same seventeen for every run, whatever the search was.
That is a deliberate change from what this module used to do: the sheet was the
**portal's own Export-to-Excel file** passed through with its columns in the
order the portal emitted them, on the reasoning that a passthrough cannot drift
when the portal changes. What that actually delivered was a sheet whose shape
depended on the search, missing everything the portal's export does not carry —
no status, no commodity codes, no contact, no version, no link back to the ad.
A reviewer comparing two runs was comparing two different spreadsheets.

So the sheet is now built from what the scraper read: the results grid for the
identifiers and the posting window, the detail page for everything else (see
`myflorida/detail.py`). The portal's own export is still downloaded and still
staged under `_exports/`, but nothing is built from it.

**No row is dropped and no row is judged.** Every advertisement the search
returned reaches this sheet, with no score, no verdict and no accept/reject
column — deciding what is worth pursuing is the reviewer's job, not the
scraper's.

`build_summary_at` is also what rebuilds the sheet from the database months
later (see `sweep/export.generate_excel`), so a download long after the run
matches what shipped in the ZIP.
"""

import logging
from pathlib import Path

from app.core import excel_style
from app.scrapers.myflorida import storage

logger = logging.getLogger(__name__)

# The summary sheet's columns, in the order a reviewer scans them: what the ad
# is called and numbered, who wants it, what state it is in, when it opens and
# closes, what it is for, who to ask, and where to read it in full.
#
# (record key, column header). The record keys are `detail.parse`'s field names
# plus `document_count`, so the sheet and the parser cannot drift apart.
RECORD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("ad_number", "Advertisement Number"),
    ("agency_ad_number", "Agency Advertisement Number"),
    ("version", "Version Number"),
    ("title", "Title"),
    ("ad_type", "Advertisement Type"),
    ("agency", "Agency"),
    ("status", "Status"),
    ("open_date", "Open Date"),
    ("close_date", "Closing Date"),
    ("published_date", "Published Date"),
    ("commodity_codes", "Commodity Codes"),
    ("contact_name", "Contact Person"),
    ("contact_email", "Contact Email"),
    ("contact_phone", "Contact Phone"),
    ("description", "Description"),
    ("document_count", "Documents"),
    ("detail_url", "Detail Page URL"),
    # The evaluation, last: a reader identifies a bid before anything judges it,
    # and the verdict is a column rather than a filter — no row is ever dropped
    # for what the engine made of it. See `myflorida/evaluation.py`.
    ("decision", "Evaluation Status"),
    ("evaluation_reason", "Evaluation Reason"),
    ("ai_notes", "AI Notes"),
)

#: How a row is tinted, by what its verdict means. Returned to
#: `excel_style.write_table`, which owns the palette.
#:
#: REJECT is the client's own pure red (FFFF0000) — the criteria document names
#: that colour because it is the mark they already make by hand. MANUAL_REVIEW
#: is yellow and means exactly one thing: nobody has decided this, neither the
#: rules nor the model, so a person still has to look. PURSUE is left clean,
#: which is what makes the other two visible at all.
_ROW_TINT = {
    "REJECT": "client_reject",
    "MANUAL_REVIEW": "review",
}


def _row_style(values: list) -> str | None:
    """The tint for one written row, read off the cell that was written.

    Read back rather than re-derived from the record: a second pass could
    disagree with the first, and a row filled red whose Evaluation Status says
    PURSUE is worse than either answer on its own.
    """
    decision = str(values[_DECISION_INDEX] or "").strip().upper()
    return _ROW_TINT.get(decision)


#: Where Evaluation Status lands in a written row. Taken from the column list
#: rather than hardcoded, so inserting a column cannot move the tint onto the
#: wrong cell.
_DECISION_INDEX = next(i for i, (key, _) in enumerate(RECORD_COLUMNS) if key == "decision")


def _cell(record: dict, key: str):
    """One column's value for one advertisement.

    `document_count` is the only computed column — the sheet carries how many
    attachments the ad had, and the files themselves are in the ZIP beside it.
    """
    if key == "document_count":
        return len(record.get("documents") or [])
    return record.get(key)


def build_summary_at(records: list[dict], out_path: Path) -> int:
    """Write the captured advertisements to `out_path` as the summary sheet.

    Returns the row count. Every record is written; nothing is filtered, ranked
    or scored on the way in. Used both for the copy inside the archive and for
    rebuilding that same sheet from the database later, so a download months on
    matches what shipped.
    """
    workbook, sheet = excel_style.new_workbook("Bids")
    rows = ([_cell(record, key) for key, _ in RECORD_COLUMNS] for record in records)
    count = excel_style.write_table(
        sheet,
        [header for _, header in RECORD_COLUMNS],
        rows,
        row_style=_row_style,
    )
    workbook.save(str(out_path))
    logger.info("wrote %d captured bid(s) to %s", count, Path(out_path).name)
    return count


def build_from_records(records: list[dict], run_dir: Path) -> Path:
    """The summary sheet in its place at the root of the run's export folder.

    The one way a run's sheet is built, for both the niche flow and the sweep,
    and the reason a run always ships an index — a summary is not optional, it
    is the archive's index.
    """
    target = storage.summary_path(run_dir)
    build_summary_at(records, target)
    return target
