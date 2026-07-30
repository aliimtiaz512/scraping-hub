"""Per-bid document handling: download → extract text → delete.

The sweep does not keep attachments. Their only purpose is to feed the scope
signal (criteria doc §3.3), so each bid's files land in a scratch folder, their
text is extracted, and the folder is removed — the same shape as SAM's engine,
which deletes downloads once their text has been lifted.

Text extraction itself is SAM's `build_full_text`, imported rather than
reimplemented: it already handles .pdf via PyMuPDF, .docx via python-docx with
old-binary-.doc detection, and .txt, skipping everything else.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from app.scrapers.sam.engine.text_extractor import build_full_text

logger = logging.getLogger(__name__)

# Where a bid's attachments live while their text is being extracted. Sits
# inside the run workspace so a crashed run's leftovers are cleaned up with it.
SCRATCH_DIRNAME = "_evaluation"


@dataclass
class DocumentText:
    """What one bid's attachments contributed to its scope signal."""

    filenames: list[str] = field(default_factory=list)
    text: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        """Surfaced in the workbook: 0 means the bid was judged without
        attachment evidence — a scanned-image PDF yields nothing, and without
        this the reader cannot tell that apart from a bid with 40 pages."""
        return len(self.text)


def scratch_dir(run_dir: Path, ad_number: str) -> Path:
    """A private folder for one bid's downloads."""
    from app.core.filenames import sanitize_filename

    folder = run_dir / SCRATCH_DIRNAME / sanitize_filename(ad_number or "unknown", max_length=64)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def extract_and_discard(folder: Path, description: str) -> DocumentText:
    """Combine the description with the folder's document text, then delete it.

    The folder is removed whatever happens — an extraction failure must not
    leave attachments on disk, since the sweep's whole delivery contract is one
    spreadsheet and nothing else.
    """
    result = DocumentText()
    try:
        result.filenames = sorted(p.name for p in folder.iterdir() if p.is_file())
        result.text = build_full_text(description, folder)
    except Exception as exc:  # noqa: BLE001 — a parse failure must not drop the bid
        logger.exception("document extraction failed for %s", folder)
        result.errors.append(f"{exc.__class__.__name__}: {exc}")
        result.text = description or ""
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return result


def cleanup_scratch(run_dir: Path) -> None:
    """Remove the whole scratch tree at the end of a run."""
    shutil.rmtree(run_dir / SCRATCH_DIRNAME, ignore_errors=True)
