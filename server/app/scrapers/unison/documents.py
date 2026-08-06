"""Downloading a buy's attachments.

Each attachment on a detail page is a real link —
`<a href="/fbweb/viewAtt.do?token=…">ATTACHMENT_1_….pdf</a>` — so the files are
fetched over the browser's session with `requests`, carrying Selenium's cookies,
rather than by driving Chrome's download machinery. That gives a deterministic
filename and destination per file, no new-tab handling (the links are
`target="_blank"`), and no polling a download directory for `.crdownload` to
disappear. SAM downloads through the browser only because its attachment links
have no href to fetch.

The page also has a "Download Attachments" button that zips the lot
(`/fbweb/buydownloadall.do?token=…`). It is not used: one zip per buy would have
to be unpacked again before the text extractor could read it, and a single
failure would cost every file rather than one.

The files are a means, not an output. They are fetched into a scratch directory
outside the run's workspace, read into the text the evaluator sees, and deleted
with that directory when the run ends — a bid's attachments have no use once its
verdict exists, and the run delivers the spreadsheet alone. (Same shape as SAM,
which discards its downloads for the same reason.) What survives in the report
is the attachment *names*, so a reader can tell what a buy carried and open its
Detail URL if they want the files themselves.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import requests

from app.core.filenames import sanitize_filename

logger = logging.getLogger(__name__)

# Per-file timeouts: (connect, read). A bid package can be a large drawing set,
# so the read budget is generous while a dead host still fails fast.
TIMEOUT = (15, 180)
# Refuse a file that is implausibly large rather than filling the disk.
MAX_BYTES = 250 * 1024 * 1024
CHUNK = 64 * 1024


def session_from_driver(driver) -> requests.Session:
    """A `requests` session carrying the logged-in browser's cookies.

    The attachment URLs are session-authenticated: fetched without these the
    portal returns its login page with a 200, which is why the caller checks
    what came back rather than trusting the status code.
    """
    session = requests.Session()
    for cookie in driver.get_cookies():
        session.cookies.set(
            cookie["name"], cookie["value"],
            domain=cookie.get("domain"), path=cookie.get("path", "/"),
        )
    session.headers.update({"User-Agent": driver.execute_script("return navigator.userAgent;")})
    return session


def _looks_like_html(first_chunk: bytes) -> bool:
    """True if the response body is a web page — i.e. the portal served the
    login screen or an error instead of the file."""
    head = first_chunk[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def download(
    session: requests.Session,
    attachments: list[dict[str, Any]],
    target_dir: Path,
) -> list[dict[str, Any]]:
    """Fetch every attachment into `target_dir`.

    Returns the attachment records with a `file` (the saved name) or an `error`
    added. One file failing costs that file: the rest of the buy's documents,
    and the buy itself, carry on.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, Any]] = []

    for index, attachment in enumerate(attachments, start=1):
        record = dict(attachment)
        url, name = attachment.get("url"), attachment.get("name") or f"attachment_{index}"
        if not url:
            record["error"] = "no download link"
            saved.append(record)
            continue

        destination = target_dir / _unique_name(target_dir, name)
        try:
            with session.get(url, timeout=TIMEOUT, stream=True) as response:
                response.raise_for_status()
                written = 0
                with open(destination, "wb") as handle:
                    for chunk in response.iter_content(CHUNK):
                        if not chunk:
                            continue
                        if written == 0 and _looks_like_html(chunk):
                            raise ValueError(
                                "the portal returned a web page, not a file — "
                                "the session is probably no longer signed in"
                            )
                        written += len(chunk)
                        if written > MAX_BYTES:
                            raise ValueError(f"file exceeds {MAX_BYTES // (1024 * 1024)}MB")
                        handle.write(chunk)
            record["file"] = destination.name
            record["bytes"] = written
            logger.info("downloaded %s (%d bytes)", destination.name, written)
        except Exception as exc:  # noqa: BLE001 — one attachment must not sink the buy
            destination.unlink(missing_ok=True)
            record["error"] = str(exc)[:200]
            logger.warning("could not download %s: %s", name, exc)
        saved.append(record)

    return saved


def _unique_name(folder: Path, name: str) -> str:
    """A filesystem-safe name that doesn't overwrite a sibling of the same name."""
    safe = sanitize_filename(name, max_length=120) or "attachment"
    candidate = Path(safe)
    stem, suffix = candidate.stem, candidate.suffix
    result, counter = safe, 2
    while (folder / result).exists():
        result = f"{stem} ({counter}){suffix}"
        counter += 1
    return result


def extract_text(folder: Path) -> str:
    """The combined plain text of every downloaded document in `folder`.

    Delegates to the SAM engine's extractor — PDF via PyMuPDF, DOCX via
    python-docx, TXT read directly, anything else skipped — so both portals read
    documents the same way.
    """
    if not folder.is_dir():
        return ""
    try:
        from app.scrapers.sam.engine.text_extractor import extract_text_from_folder

        return extract_text_from_folder(folder) or ""
    except Exception:  # noqa: BLE001 — unreadable documents must not fail the bid
        logger.exception("could not extract document text from %s", folder)
        return ""
