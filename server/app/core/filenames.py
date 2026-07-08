"""Filename and timestamp helpers shared across scrapers."""

import re
from datetime import datetime


def sanitize_filename(name: str, max_length: int = 120) -> str:
    """Make an arbitrary string safe to use as a folder/file name."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" ._")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:max_length] or "untitled"


def timestamp(fmt: str = "%Y-%m-%d %H-%M-%S") -> str:
    """Current local time as a filesystem-safe string (no colons)."""
    return datetime.now().strftime(fmt)
