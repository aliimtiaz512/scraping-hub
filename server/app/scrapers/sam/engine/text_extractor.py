"""
SAM.gov Scraper — document text extractor.

Reads every file inside a  temp_docs/<notice_id>/  folder and returns
the combined plain text.  Supported formats:

  .pdf   — extracted via PyMuPDF (fitz)
  .docx  — extracted via python-docx
  .txt   — read directly
  others — skipped with a debug log

The result is intended to be concatenated with the bid description that
was already scraped from the detail page to form a single "full text"
field used downstream for AI analysis.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-format extractors
# ---------------------------------------------------------------------------

def _extract_pdf(path: Path) -> str:
    """Extract all text from a PDF using PyMuPDF."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(path))
        parts = []
        for page in doc:
            text = page.get_text("text")
            if text.strip():
                parts.append(text.strip())
        doc.close()
        return "\n".join(parts)
    except Exception as exc:
        logger.warning(f"PDF extraction failed for {path.name}: {exc}")
        return ""


_OLE_MAGIC = b"\xd0\xcf\x11\xe0"  # Old binary .doc format (OLE Compound Document)


def _extract_docx(path: Path) -> str:
    """
    Extract all paragraph text from a DOCX file using python-docx.
    Falls back gracefully if the file is an old binary .doc format.
    """
    # Detect old binary .doc masquerading as .docx (OLE Compound Document)
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
        if magic == _OLE_MAGIC:
            logger.warning(
                f"Skipping {path.name}: file is old binary .doc format, "
                f"not a valid .docx — cannot extract text."
            )
            return ""
    except Exception:
        pass

    try:
        from docx import Document
        doc = Document(str(path))
        parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        return "\n".join(parts)
    except Exception as exc:
        logger.warning(f"DOCX extraction failed for {path.name}: {exc}")
        return ""


def _extract_txt(path: Path) -> str:
    """Read a plain text file, trying UTF-8 then latin-1."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="latin-1").strip()
        except Exception as exc:
            logger.warning(f"TXT read failed for {path.name}: {exc}")
            return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_text_from_folder(folder: Path) -> str:
    """
    Extract and concatenate text from all supported files in *folder*.

    Parameters
    ----------
    folder : Path
        Directory containing downloaded attachment files for one bid.

    Returns
    -------
    Combined plain text (may be empty string if folder is empty or all
    files are unsupported / unreadable).
    """
    if not folder.exists() or not folder.is_dir():
        return ""

    files = sorted(folder.iterdir())
    if not files:
        return ""

    parts: list[str] = []

    for path in files:
        if not path.is_file():
            continue

        suffix = path.suffix.lower()

        if suffix == ".pdf":
            text = _extract_pdf(path)
        elif suffix == ".docx":
            text = _extract_docx(path)
        elif suffix == ".txt":
            text = _extract_txt(path)
        else:
            logger.debug(f"Skipping unsupported file type: {path.name}")
            continue

        if text:
            parts.append(f"=== {path.name} ===\n{text}")
            logger.debug(f"  Extracted {len(text):,} chars from {path.name}")
        else:
            logger.debug(f"  No text extracted from {path.name}")

    return "\n\n".join(parts)


def build_full_text(description: str, docs_folder: Path) -> str:
    """
    Combine the scraped bid description with text extracted from all
    downloaded attachment files.

    Parameters
    ----------
    description  : The 'Description' field already scraped from the detail page.
    docs_folder  : Path to  temp_docs/<notice_id>/

    Returns
    -------
    Single string:  description block  +  attachment text blocks.
    The description is always first; attachment blocks follow, each
    prefixed with the filename as a heading.
    """
    parts: list[str] = []

    if description and description.strip():
        parts.append(f"=== Description ===\n{description.strip()}")

    docs_text = extract_text_from_folder(docs_folder)
    if docs_text:
        parts.append(docs_text)

    return "\n\n".join(parts)
