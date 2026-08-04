"""Attachment detection and download for BidNet Direct solicitations.

Two jobs, deliberately split apart:

**Detection** — find every attachment on a solicitation's detail page. BidNet
renders the "Documents" tab body *lazily*: until the tab is clicked the
attachment anchors are simply not in the DOM (verified on live pages — a bid
showing "Documents (2)" has zero `attachmentDownloadLnk` anchors before the
click and two after). So detection has to click the tab and then **wait for the
anchors themselves**, which is the whole fix for the false-zero counts:

  * the old code gated the entire download phase on the tab's count badge, so
    any page whose badge was unreadable — a slow render, a re-layout, a
    `.tabCount` that had not been filled in yet — reported `0` and the bid's
    documents were never even looked for;
  * and after clicking the tab it slept a flat 5 seconds and took whatever was
    in the DOM. When the tab's AJAX took longer than that (a large document
    list, an aged session, a slow portal) it found nothing and the bid recorded
    zero documents despite the badge saying otherwise.

Now the badge is only ever a *cross-check* to log against — never a gate — and
the wait is an explicit condition on the anchors appearing, so a slow tab costs
a few extra seconds instead of an entire bid's attachments.

**Download** — fetch what detection found. The anchors carry real, direct hrefs
(`/private/solicitations/<id>/abstract/docs-items/<doc>/attachment-download`),
so there is no reason to drive the browser for this. We hand the session cookies
to a `requests.Session` and fetch the files over plain HTTP, several at a time,
streamed to disk in chunks. That replaces a serial click-and-wait-for-Chrome
loop (one download at a time, each polling the filesystem for a `.crdownload`
to settle) with concurrent streamed GETs, and it is where nearly all of the
per-bid time was going.

Attachments are not inside iframes: the only frames on a solicitation page are
third-party (ShareThis, Drift chat), which is why no frame traversal happens
here.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import requests
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from app.core.filenames import sanitize_filename

logger = logging.getLogger(__name__)

BASE_URL = "https://www.bidnetdirect.com"

# The documents tab and its lazily-rendered attachment anchors. Both the desktop
# tab and the `_mobile` duplicate are accepted: the portal renders both and hides
# one by CSS, and which is visible depends on the viewport the run happens to use.
DOCS_TAB_SELECTORS = (
    "#docs-itemsAbstractTab a",
    "#docs-itemsAbstractTab_mobile a",
    "a[href*='innerTabId=docs-items']",
)
# Every attachment link the portal renders. The id prefix is the reliable one;
# the href match also catches any anchor rendered without that id.
ATTACHMENT_SELECTOR = "a[id^='attachmentDownloadLnk'], a[href*='attachment-download']"

# How long to wait for the tab's AJAX to put the attachment anchors in the DOM.
# Generously above what a healthy page takes, because the cost of waiting is a
# few seconds and the cost of giving up early is the bid's entire document set.
TAB_RENDER_TIMEOUT = 30
# Confirmation wait for a bid whose badge positively reads "0". Short, because
# there is nothing to wait for — but not skipped, so a mis-rendered badge still
# gets one look at the tab body.
ZERO_BADGE_TIMEOUT = 4
# Poll interval while waiting for those anchors.
TAB_POLL = 0.25
# Per-file HTTP timeout: (connect, read). Read applies per chunk, not to the
# whole transfer, so a large but steadily-streaming PDF is never cut off.
HTTP_TIMEOUT = (15, 60)
# Concurrent downloads per bid. Bid packages are typically 1-12 files; four at a
# time saturates the portal's per-file throughput without hammering it.
MAX_PARALLEL_DOWNLOADS = 4
# Streaming chunk size — large files go to disk incrementally rather than being
# held in memory in full.
CHUNK_SIZE = 256 * 1024
# Retries per file. A single transient 5xx/reset should not lose an attachment.
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_BACKOFF = 1.5


@dataclass
class DocumentLink:
    """One attachment found on a solicitation page."""

    url: str
    # The anchor's visible text, which on BidNet is the real filename
    # ("Addendum 1.pdf"). Used when the response carries no Content-Disposition.
    label: str

    @property
    def fallback_name(self) -> str:
        name = sanitize_filename(self.label) if self.label.strip() else ""
        if name:
            return name
        tail = unquote(urlparse(self.url).path.rstrip("/").split("/")[-1])
        return sanitize_filename(tail) if tail else "document"


@dataclass
class DownloadOutcome:
    """What actually happened for one bid's attachments."""

    detected: int = 0
    saved: list[str] = None  # type: ignore[assignment]
    failed: list[str] = None  # type: ignore[assignment]
    seconds: float = 0.0
    # The tab's own count badge, when it could be read — a cross-check only.
    badge: str | None = None
    # Byte-identical copies removed after download (see `_dedupe`).
    duplicates: int = 0

    def __post_init__(self) -> None:
        self.saved = self.saved if self.saved is not None else []
        self.failed = self.failed if self.failed is not None else []

    @property
    def downloaded(self) -> int:
        return len(self.saved)

    @property
    def distinct(self) -> int:
        """Detected links minus the copies that proved byte-identical — the
        number of real documents the solicitation actually offers."""
        return self.detected - self.duplicates


def _abs_url(href: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return BASE_URL + href
    return href


def read_badge(driver) -> str | None:
    """The number the Documents tab shows, or None if it cannot be read.

    Purely informational. It is never used to decide whether to look for
    documents — reading it as a gate is precisely what produced false zeros.
    """
    script = """
    const ids = ['docs-itemsAbstractTab', 'docs-itemsAbstractTab_mobile'];
    for (const id of ids) {
      const tab = document.getElementById(id);
      if (!tab) continue;
      const badge = tab.querySelector('.tabCount');
      // textContent, not innerText: a tab hidden by CSS (the mobile/desktop
      // pair means one always is) has no rendered text, and reading innerText
      // there returns "" — which used to be recorded as "0 documents".
      const text = badge ? (badge.textContent || '') : (tab.textContent || '');
      const digits = text.replace(/[^0-9]/g, '');
      if (digits !== '') return digits;
    }
    return null;
    """
    try:
        value = driver.execute_script(script)
    except WebDriverException:
        return None
    return str(value) if value not in (None, "") else None


def _find_attachments(driver) -> list[DocumentLink]:
    """Every attachment anchor currently in the DOM."""
    script = """
    return [...document.querySelectorAll(arguments[0])].map(a => ({
      href: a.getAttribute('href') || a.href || '',
      text: (a.innerText || a.textContent || '').trim(),
    })).filter(a => a.href && !a.href.toLowerCase().startsWith('javascript'));
    """
    try:
        raw = driver.execute_script(script, ATTACHMENT_SELECTOR) or []
    except WebDriverException as exc:
        logger.info("attachment scan failed: %s", exc.__class__.__name__)
        return []

    links: list[DocumentLink] = []
    seen: set[str] = set()
    for item in raw:
        url = _abs_url(item.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        links.append(DocumentLink(url=url, label=item.get("text") or ""))
    return links


def _open_documents_tab(driver) -> bool:
    """Click the Documents tab so the portal renders its body. True if clicked."""
    for selector in DOCS_TAB_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue
        for element in elements:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
                # A JS click, not element.click(): the tab anchors sit under a
                # sticky header on some layouts, where a native click lands on
                # the header instead and the tab body is never requested.
                driver.execute_script("arguments[0].click();", element)
                return True
            except WebDriverException:
                continue
    return False


def extract_document_links(
    driver,
    run_id: str,
    reference: str,
    detail_url: str = "",
    timeout: int = TAB_RENDER_TIMEOUT,
) -> tuple[list[DocumentLink], str | None]:
    """Find every attachment on the currently-loaded solicitation page.

    Returns (links, badge). The badge is the tab's own count, reported for
    logging and reconciliation only.

    The tab body is lazy: the anchors do not exist until the tab is clicked, so
    this clicks it and then waits for the anchors themselves rather than
    sleeping a fixed interval and hoping. If the click yields nothing, it falls
    back to loading the tab's own URL (`?innerTabId=docs-items`), which renders
    the same list server-side — covering the case where the tab's JS handler
    never fires.
    """
    badge = read_badge(driver)
    target = int(badge) if badge is not None and badge.isdigit() else None

    # A page can already carry *some* anchors before the tab is opened — on live
    # bids we saw one anchor present with two in the finished list. So a
    # non-empty pre-click scan is only trusted when it already accounts for
    # every file the badge promises; otherwise the tab is opened anyway and the
    # two sets are merged. Returning early on a partial pre-render would quietly
    # download one of two documents, which is the same class of bug as counting
    # zero of two.
    found = _find_attachments(driver)
    if found and target is not None and len(found) >= target:
        return found, badge

    # A badge that positively reads "0" is trustworthy — unlike an *unreadable*
    # badge, which is what the old code silently turned into "0 documents". Open
    # the tab and confirm briefly, but never spend the full wait (or the extra
    # page load below) on a solicitation that genuinely has no attachments.
    if target == 0:
        if _open_documents_tab(driver):
            found = _merge(found, _wait_for_attachments(driver, ZERO_BADGE_TIMEOUT, None))
        return found, badge

    if _open_documents_tab(driver):
        found = _merge(found, _wait_for_attachments(driver, timeout, target))
        if found and (target is None or len(found) >= target):
            return found, badge

    # Fallback: ask for the documents tab directly. Cheap, and it recovers a
    # page whose tab script never ran — or one whose list came back short.
    fallback = _docs_tab_url(driver, detail_url)
    if fallback:
        logger.info(
            "[run %s] %s: %d attachment(s) after the tab click%s — retrying via %s",
            run_id, reference or "bid", len(found),
            f" against a badge of {target}" if target is not None else "", fallback,
        )
        try:
            driver.get(fallback)
            found = _merge(found, _wait_for_attachments(driver, timeout, target))
        except WebDriverException as exc:
            logger.info(
                "[run %s] %s: documents-tab fallback failed: %s",
                run_id, reference or "bid", exc.__class__.__name__,
            )

    return found, badge


def _merge(*groups: list[DocumentLink]) -> list[DocumentLink]:
    """Union of several attachment scans, de-duplicated by URL, order kept."""
    merged: list[DocumentLink] = []
    seen: set[str] = set()
    for group in groups:
        for link in group:
            if link.url not in seen:
                seen.add(link.url)
                merged.append(link)
    return merged


def _wait_for_attachments(
    driver, timeout: int, target: int | None = None
) -> list[DocumentLink]:
    """Block until the attachment anchors are all present, or `timeout` expires.

    This replaces the old fixed `time.sleep(5)`: a tab whose AJAX is slow now
    costs the extra seconds it needs instead of silently yielding an empty
    document list. When the tab's badge told us how many files to expect, the
    wait holds out for that many — a list that renders row by row would
    otherwise be read while it was still half-drawn.
    """
    found: list[DocumentLink] = []

    def _ready(_d) -> bool:
        nonlocal found
        found = _find_attachments(driver)
        if not found:
            return False
        return len(found) >= target if target else True

    try:
        WebDriverWait(driver, timeout, poll_frequency=TAB_POLL).until(_ready)
    except (TimeoutException, WebDriverException):
        pass
    if found:
        # Even having hit the target, give a late row a moment to join: the
        # badge is not always exact, so the count settling is the real signal.
        found = _settle(driver, found)
    return found


def _settle(
    driver, current: list[DocumentLink], rounds: int = 3, pause: float = 0.4
) -> list[DocumentLink]:
    """Re-scan until the attachment count stops growing."""
    for _ in range(rounds):
        time.sleep(pause)
        links = _find_attachments(driver)
        if len(links) <= len(current):
            return current if len(links) < len(current) else links
        current = links
    return current


def _docs_tab_url(driver, detail_url: str) -> str | None:
    """The `?innerTabId=docs-items` URL for the current solicitation."""
    try:
        href = driver.execute_script(
            "const a = document.querySelector(\"a[href*='innerTabId=docs-items']\");"
            "return a ? a.getAttribute('href') : null;"
        )
    except WebDriverException:
        href = None
    if href:
        return _abs_url(href)

    current = detail_url or (driver.current_url or "")
    match = re.search(r"/solicitations/(\d+)", current)
    if match:
        return f"{BASE_URL}/private/solicitations/{match.group(1)}/abstract?innerTabId=docs-items"
    return None


# -- downloading ------------------------------------------------------------


def build_session(driver, session: requests.Session | None = None) -> requests.Session:
    """A `requests` session carrying the browser's cookies.

    The attachment URLs are ordinary authenticated GETs, so once the logged-in
    cookies are copied across, `requests` can fetch them directly — no browser
    involved. Reusing one session across the run also keeps connections pooled.
    """
    session = session or requests.Session()
    try:
        for cookie in driver.get_cookies():
            session.cookies.set(
                cookie["name"], cookie["value"], domain=cookie.get("domain"), path=cookie.get("path", "/")
            )
    except WebDriverException as exc:
        logger.info("could not copy cookies to the HTTP session: %s", exc.__class__.__name__)
    try:
        user_agent = driver.execute_script("return navigator.userAgent;")
    except WebDriverException:
        user_agent = None
    session.headers.update(
        {
            "User-Agent": user_agent or "Mozilla/5.0",
            "Accept": "*/*",
        }
    )
    return session


_FILENAME_STAR = re.compile(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)", re.I)
_FILENAME = re.compile(r'filename\s*=\s*"?([^";]+)"?', re.I)


def filename_from_response(response, fallback: str) -> str:
    """The server's filename for a download, falling back to the link text."""
    disposition = response.headers.get("Content-Disposition", "") or ""
    match = _FILENAME_STAR.search(disposition) or _FILENAME.search(disposition)
    if match:
        name = sanitize_filename(unquote(match.group(1).strip()))
        if name:
            return name
    return fallback


# Serialises filename reservation. Downloads run in parallel and BidNet does
# reuse names across a bid's attachments ("Addendum 1.pdf" twice), so two
# threads can pick the same target: without this they would open the same
# `.part` file and interleave two documents into one corrupt file.
_RESERVE_LOCK = threading.Lock()


def _reserve(folder: Path, name: str) -> tuple[Path, Path]:
    """Claim a free filename, returning (final path, .part path).

    The `.part` file is created inside the lock, which is what makes the claim
    stick: a second thread wanting the same name sees it and takes the next
    suffix instead.
    """
    base = folder / name
    stem, suffix = base.stem, base.suffix
    with _RESERVE_LOCK:
        counter = 1
        while True:
            candidate = base if counter == 1 else folder / f"{stem} ({counter}){suffix}"
            partial = candidate.with_suffix(candidate.suffix + ".part")
            if not candidate.exists() and not partial.exists():
                partial.touch()
                return candidate, partial
            counter += 1


def _fetch_one(
    session: requests.Session,
    link: DocumentLink,
    folder: Path,
    referer: str,
    should_stop: Callable[[], None] | None,
) -> tuple[str | None, str | None]:
    """Stream one attachment to disk. Returns (saved name, error message)."""
    delay = DOWNLOAD_BACKOFF
    last_error = "unknown error"
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        if should_stop:
            should_stop()
        partial: Path | None = None
        try:
            headers = {"Referer": referer} if referer else {}
            with session.get(
                link.url, headers=headers, stream=True, timeout=HTTP_TIMEOUT, allow_redirects=True
            ) as response:
                if response.status_code != 200:
                    last_error = f"HTTP {response.status_code}"
                    raise requests.RequestException(last_error)

                name = filename_from_response(response, link.fallback_name)
                # Stream through a .part file so an interrupted transfer never
                # leaves a truncated document looking like a complete one, and
                # so a name shared by two attachments cannot collide mid-write.
                target, partial = _reserve(folder, name)
                size = 0
                with partial.open("wb") as handle:
                    for chunk in response.iter_content(CHUNK_SIZE):
                        if chunk:
                            handle.write(chunk)
                            size += len(chunk)

                if size == 0:
                    last_error = "empty response body"
                    raise requests.RequestException(last_error)

                expected = response.headers.get("Content-Length")
                if expected and expected.isdigit() and int(expected) != size:
                    last_error = f"truncated ({size} of {expected} bytes)"
                    raise requests.RequestException(last_error)

                partial.rename(target)
                return target.name, None
        except Exception as exc:  # noqa: BLE001 — one bad file must not lose the rest
            if partial is not None:
                partial.unlink(missing_ok=True)
            last_error = last_error if last_error != "unknown error" else f"{exc.__class__.__name__}: {exc}"
            if attempt < DOWNLOAD_ATTEMPTS:
                time.sleep(delay)
                delay *= 2
                continue
    return None, last_error


def _digest(path: Path) -> str:
    """SHA-256 of a file, read in chunks so a large one never loads in full."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _dedupe(folder: Path, names: list[str]) -> tuple[list[str], int]:
    """Drop byte-identical copies from a bid's folder, keeping the first.

    A solicitation lists the same file in more than one place — the documents
    table and the line-items table — under two different document ids, so the
    two anchors are genuinely distinct URLs serving identical bytes. Detection
    deliberately keeps both (scoping it to one table would lose an attachment
    that only ever appears in the other), so the duplicate is resolved here,
    where the content itself settles the question. Size is checked first, so a
    hash is only computed for files that could actually be duplicates.
    """
    by_size: dict[int, list[tuple[str, str]]] = {}
    kept: list[str] = []
    removed = 0
    for name in names:
        path = folder / name
        try:
            size = path.stat().st_size
        except OSError:
            kept.append(name)
            continue
        bucket = by_size.setdefault(size, [])
        if not bucket:
            bucket.append((name, ""))
            kept.append(name)
            continue
        try:
            digest = _digest(path)
            for index, (other_name, other_digest) in enumerate(bucket):
                if not other_digest:
                    other_digest = _digest(folder / other_name)
                    bucket[index] = (other_name, other_digest)
                if other_digest == digest:
                    path.unlink(missing_ok=True)
                    removed += 1
                    break
            else:
                bucket.append((name, digest))
                kept.append(name)
        except OSError:
            kept.append(name)
    return kept, removed


def download_documents(
    session: requests.Session,
    links: list[DocumentLink],
    folder: Path,
    run_id: str,
    reference: str,
    referer: str = "",
    should_stop: Callable[[], None] | None = None,
    max_parallel: int = MAX_PARALLEL_DOWNLOADS,
) -> DownloadOutcome:
    """Download every detected attachment into `folder`, concurrently.

    Files stream to disk as they arrive, so a 200 MB drawing set never sits in
    memory, and a bid's attachments are fetched several at a time instead of one
    click-and-wait at a time.
    """
    outcome = DownloadOutcome(detected=len(links))
    if not links:
        return outcome

    folder.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    workers = max(1, min(max_parallel, len(links)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(lambda l: _fetch_one(session, l, folder, referer, should_stop), links)
        )
    outcome.seconds = time.monotonic() - started

    for link, (name, error) in zip(links, results):
        if name:
            outcome.saved.append(name)
        else:
            outcome.failed.append(f"{link.fallback_name}: {error}")

    kept, outcome.duplicates = _dedupe(folder, outcome.saved)
    outcome.saved = kept

    label = reference or "bid"
    # `detected` counts the links; the duplicates the portal lists twice are not
    # separate documents, so the "N/N" the log reports is against the distinct
    # ones — otherwise a clean run would read as permanently one file short.
    distinct = outcome.detected - outcome.duplicates
    logger.info(
        "[run %s] Found %d documents for %s | Downloaded %d/%d in %.1fs%s",
        run_id, distinct, label, outcome.downloaded, distinct, outcome.seconds,
        f" ({outcome.duplicates} duplicate copy/copies removed)" if outcome.duplicates else "",
    )
    if outcome.failed:
        # Detected but not retrieved is the case worth shouting about: the bid
        # looks complete in the spreadsheet while its folder is short a file.
        logger.warning(
            "[run %s] %s: %d of %d detected document(s) could not be downloaded — %s",
            run_id, label, len(outcome.failed), outcome.detected, "; ".join(outcome.failed),
        )
    return outcome
