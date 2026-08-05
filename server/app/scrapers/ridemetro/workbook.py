"""The agency-grouped report: one sheet, one block per agency.

    ┌──────────────────────────────────────────────────────────────┐
    │ CITY OF DALLAS                                               │  banner (merged, bold, filled)
    ├────────┬────────┬─────────┬───────────┬─────────┬─────┬──────┤
    │ Status │ Ref #  │ Project │ Department│ Closing │ Days│ URL  │  column headers
    ├────────┼────────┼─────────┼───────────┼─────────┼─────┼──────┤
    │ …data rows for that agency…                                  │
    └──────────────────────────────────────────────────────────────┘
                                                                       blank separator row
    ┌──────────────────────────────────────────────────────────────┐
    │ HOUSTON CITY COLLEGE                                         │
    …

Agencies that were visited and had nothing open still get a block, with a note
in place of the rows: "visited, nothing open" and "not in the report at all"
are different facts, and a report that only shows the former is the one a
reader can trust. Skipped (Incomplete) agencies are listed the same way, so the
sheet accounts for every row of My Network.

The column headers, cell sanitising and column widths are the hub's standard
(see app/core/excel_style), so this report's rows read like every other portal's
sheet. What is RideMetro's own is the banner above each block, in the same navy
a shade larger, and the note that stands in for an agency with no rows.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Sequence

from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from app.core import excel_style
from app.scrapers.ridemetro.models import SHEET_COLUMNS

logger = logging.getLogger(__name__)

SHEET_TITLE = "Open Opportunities"

_BANNER_FILL = PatternFill("solid", fgColor=excel_style.HEADER_COLOR)
_BANNER_FONT = Font(bold=True, color="FFFFFF", size=14)
_BANNER_ALIGN = Alignment(horizontal="left", vertical="center", indent=1)
_BANNER_HEIGHT = 26

_DATA_ALIGN = Alignment(vertical="top", wrap_text=True)
_URL_FONT = Font(color="1155CC", underline="single")
_NOTE_FONT = Font(italic=True, color="6B7280")
_NOTE_ALIGN = Alignment(horizontal="left", vertical="center", indent=1)

_CELL_BORDER = excel_style.CELL_BORDER


def _clean(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value if v not in (None, ""))
    return excel_style.sanitize_cell(value)


def _merge_across(sheet: Worksheet, row: int) -> None:
    sheet.merge_cells(
        start_row=row, start_column=1, end_row=row, end_column=len(SHEET_COLUMNS)
    )


def _write_banner(sheet: Worksheet, row: int, agency: str) -> None:
    """The full-width agency header."""
    _merge_across(sheet, row)
    cell = sheet.cell(row=row, column=1, value=_clean(agency))
    cell.fill = _BANNER_FILL
    cell.font = _BANNER_FONT
    cell.alignment = _BANNER_ALIGN
    # The fill only follows the merge if every covered cell carries it.
    for column in range(1, len(SHEET_COLUMNS) + 1):
        sheet.cell(row=row, column=column).fill = _BANNER_FILL
    sheet.row_dimensions[row].height = _BANNER_HEIGHT


def _write_headers(sheet: Worksheet, row: int) -> None:
    """One block's column headers, in the hub's standard header styling.

    Repeated per agency block, so it is styled row by row rather than through
    `format_excel_headers` (which formats the one header row of a plain sheet).
    """
    for column, (_, header) in enumerate(SHEET_COLUMNS, start=1):
        sheet.cell(row=row, column=column, value=header)
    excel_style.style_header_row(sheet, row, last_column=len(SHEET_COLUMNS))


def _write_record(sheet: Worksheet, row: int, record: dict[str, Any]) -> None:
    for column, (attr, _) in enumerate(SHEET_COLUMNS, start=1):
        value = _clean(record.get(attr))
        cell = sheet.cell(row=row, column=column, value=value)
        cell.alignment = _DATA_ALIGN
        cell.border = _CELL_BORDER
        if attr == "opportunity_url" and isinstance(value, str) and value.startswith("http"):
            cell.hyperlink = value
            cell.font = _URL_FONT


def _write_note(sheet: Worksheet, row: int, note: str) -> None:
    _merge_across(sheet, row)
    cell = sheet.cell(row=row, column=1, value=_clean(note))
    cell.font = _NOTE_FONT
    cell.alignment = _NOTE_ALIGN
    for column in range(1, len(SHEET_COLUMNS) + 1):
        sheet.cell(row=row, column=column).border = _CELL_BORDER


def build(
    groups: Sequence[tuple[str, Iterable[dict[str, Any]]]],
    out_path: str | Path,
    notes: dict[str, str] | None = None,
) -> int:
    """Write the report. Returns the number of opportunity rows written.

    `groups` is (agency name, its opportunity records) in the order the blocks
    should appear — normally the order My Network listed them. `notes` maps an
    agency name to the line shown in place of its rows when it has none.
    """
    notes = notes or {}
    workbook, sheet = excel_style.new_workbook(SHEET_TITLE)

    row = 1
    written = 0
    for agency, records in groups:
        _write_banner(sheet, row, agency)
        row += 1
        _write_headers(sheet, row)
        row += 1

        records = list(records)
        for record in records:
            _write_record(sheet, row, record)
            row += 1
            written += 1
        if not records:
            _write_note(sheet, row, notes.get(agency, "No open public opportunities."))
            row += 1

        row += 1  # blank separator row between agency blocks

    # Widths come last, once every block is written, so they fit the widest
    # value in the sheet. Banners are merged, so they are excluded from the
    # measurement and never stretch the first column to an agency name.
    excel_style.autofit_columns(sheet)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(str(out_path))
    logger.info(
        "wrote %s — %d agency block(s), %d opportunity row(s)",
        out_path.name, len(groups), written,
    )
    return written
