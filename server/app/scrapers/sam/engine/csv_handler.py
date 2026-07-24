"""
SAM.gov Scraper — CSV file handling.

Handles output directory resolution, file creation, live row appending,
and final DataFrame-based CSV writing.
"""

import csv
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def resolve_output_dir(csv_cfg: dict) -> Path:
    """Return the absolute output directory, creating it if needed."""
    cfg_dir = csv_cfg.get("output_dir", "sam_output")
    output_dir = Path(cfg_dir)
    if not output_dir.is_absolute():
        output_dir = Path.cwd() / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def get_csv_filename(csv_cfg: dict) -> str:
    """Generate a timestamped CSV filename: sam-2026-03-16 & 1-12am.csv"""
    now = datetime.now()
    date_part = now.strftime("%Y-%m-%d")
    hour = int(now.strftime("%I"))  # 1-12, no leading zero
    minute = now.strftime("%M")
    ampm = now.strftime("%p").lower()
    prefix = csv_cfg.get("filename_prefix", "sam-")
    return f"{prefix}{date_part} & {hour}-{minute}{ampm}.csv"


def init_csv(csv_cfg: dict, output_filename: str) -> Path:
    """
    Create the CSV file with header row.

    Called once at the start of run() so the file exists on disk
    immediately — even before a single bid is scraped.
    Returns the absolute Path to the created file.
    """
    columns = csv_cfg.get("columns", [
        "Notice Title", "Notice ID", "Department/Ind. Agency",
        "Description", "Subtier", "Updated Date",
        "Date Offers Due", "Published Date", "Office",
    ])

    output_dir = resolve_output_dir(csv_cfg)
    filepath = output_dir / output_filename

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()

    abs_path = filepath.resolve()
    logger.info(f"CSV created (headers written): {abs_path}")
    print(
        f"\n[SAM] CSV file created - rows are written instantly as they are scraped:\n"
        f"      {abs_path}\n"
    )
    return filepath


def append_row(csv_filepath: Path | None, row: dict, csv_cfg: dict) -> None:
    """
    Append a single extracted row to the CSV file instantly.

    Opens the file in append mode on every call so data is flushed to disk
    immediately — a Ctrl-C at any point preserves all previous rows.
    """
    if not csv_filepath:
        return
    columns = csv_cfg.get("columns", list(row.keys()))
    with open(csv_filepath, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writerow(row)


def save_csv(
    data: list[dict],
    csv_cfg: dict,
    output_filename: str | None,
) -> str | None:
    """
    Write all collected data to a CSV file (fallback / full-save).

    Guarantees:
      • Output directory is created automatically.
      • A file is ALWAYS written, even when data is empty.
      • If the file is locked (e.g. open in Excel), a timestamped backup is written.
    """
    output_dir = resolve_output_dir(csv_cfg)
    filename = output_filename or get_csv_filename(csv_cfg)
    filepath = output_dir / filename

    preferred = csv_cfg.get("columns", [])

    if not data:
        df = pd.DataFrame(columns=preferred)
        try:
            df.to_csv(filepath, index=False, encoding="utf-8-sig")
            abs_path = filepath.resolve()
            logger.warning(f"No rows passed all filters - empty CSV created -> {abs_path}")
            print(f"\n[SAM] [!] No bids matched all filters. Empty CSV created:\n      {abs_path}\n")
        except Exception as e:
            logger.error(f"Error creating empty CSV: {e}")
        return str(filepath)

    df = pd.DataFrame(data)
    extra = [c for c in df.columns if c not in preferred]
    df = df[[c for c in preferred + extra if c in df.columns]]

    try:
        df.to_csv(filepath, index=False, encoding="utf-8-sig")
        abs_path = filepath.resolve()
        logger.info(f"CSV saved -> {abs_path}  ({len(df)} rows)")
        print(f"\n[SAM] CSV saved ({len(df)} rows):\n      {abs_path}\n")
        return str(abs_path)

    except PermissionError:
        ts = datetime.now().strftime(csv_cfg.get("timestamp_format", "%Y%m%d_%H%M%S"))
        backup = output_dir / f"{csv_cfg.get('backup_prefix', 'sam-backup-')}{ts}.csv"
        df.to_csv(backup, index=False, encoding="utf-8-sig")
        abs_path = backup.resolve()
        logger.warning(f"Original file open - backup saved -> {abs_path}")
        print(f"\n[SAM] [!] Original file was open. Backup saved:\n      {abs_path}\n")
        return str(abs_path)

    except Exception as e:
        logger.error(f"Error saving CSV: {e}")
        print(f"\n[SAM] [ERR] Failed to save CSV: {e}\n")
        return None
