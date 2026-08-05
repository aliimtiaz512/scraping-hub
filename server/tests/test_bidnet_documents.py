"""BidNet attachment detection and download tests.

No browser and no portal: the driver is a stub that returns whatever DOM the
test describes, and downloads are served by a local HTTP server. That covers
the two failure modes this module exists to fix — a documents tab whose links
arrive late (which used to be recorded as "0 documents") and a count badge that
cannot be read (which used to skip the download phase entirely).

    server/.venv/bin/python server/tests/test_bidnet_documents.py
"""

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests  # noqa: E402
from selenium.common.exceptions import WebDriverException  # noqa: E402

from app.scrapers.bidnet import documents  # noqa: E402
from app.scrapers.bidnet.documents import (  # noqa: E402
    DocumentLink,
    download_documents,
    extract_document_links,
    filename_from_response,
)


# -- a stub driver ----------------------------------------------------------


class FakeDriver:
    """Enough of a WebDriver to exercise detection.

    `anchors` is the list of attachment anchors currently "in the DOM". A
    `reveal_after` of N means the tab's AJAX takes N seconds — before that the
    page has no anchors at all, which is exactly how BidNet behaves.
    """

    def __init__(self, anchors, badge=None, reveal_after=0.0, tab_present=True,
                 clickable=True):
        self._anchors = anchors
        self.badge = badge
        self.reveal_after = reveal_after
        self.tab_present = tab_present
        self.clickable = clickable
        self.current_url = "https://www.bidnetdirect.com/private/solicitations/123/abstract"
        self.clicked_at = None
        self.gets = []

    # anchors only become visible once the tab has been clicked and its AJAX ran
    def _visible_anchors(self):
        if self.clicked_at is None:
            return []
        if time.monotonic() - self.clicked_at < self.reveal_after:
            return []
        return self._anchors

    def execute_script(self, script, *args):
        if "tabCount" in script:
            return self.badge
        if "innerTabId=docs-items" in script and "querySelector(" in script:
            return "/private/solicitations/123/abstract?innerTabId=docs-items"
        if "scrollIntoView" in script:
            return None
        if ".click()" in script:
            if not self.clickable:
                # what Selenium actually raises when the JS click throws
                raise WebDriverException("javascript error")
            self.clicked_at = time.monotonic()
            return None
        if "querySelectorAll(arguments[0])" in script:
            return [{"href": a[0], "text": a[1]} for a in self._visible_anchors()]
        return None

    def find_elements(self, by, selector):
        if "docs-items" in selector and self.tab_present:
            return [object()]
        return []

    def get(self, url):
        self.gets.append(url)
        # the server-rendered tab URL always has the anchors
        self.clicked_at = time.monotonic()
        self.reveal_after = 0.0


TWO = [
    ("/private/solicitations/123/abstract/docs-items/1/attachment-download", "RFP Main.pdf"),
    ("/private/solicitations/123/abstract/docs-items/2/attachment-download", "Appendix.pdf"),
]


# -- detection --------------------------------------------------------------


def test_finds_lazily_rendered_attachments():
    """The anchors do not exist until the tab is clicked — the core behaviour."""
    driver = FakeDriver(TWO, badge="2")
    links, badge = extract_document_links(driver, "run", "REF-1")
    assert len(links) == 2, links
    assert badge == "2"
    assert links[0].url.startswith("https://www.bidnetdirect.com/"), links[0].url


def test_waits_out_a_slow_documents_tab():
    """A tab slower than the old fixed 5s sleep must still yield its documents.

    This is the false-zero bug: the previous code slept a flat 5 seconds after
    the click and took whatever was there, so a tab taking longer produced an
    empty list and the bid recorded 0 documents.
    """
    driver = FakeDriver(TWO, badge="2", reveal_after=6.0)
    links, _ = extract_document_links(driver, "run", "REF-1", timeout=20)
    assert len(links) == 2, f"slow tab lost its documents: {links}"


def test_unreadable_badge_does_not_suppress_detection():
    """A missing count badge must not stop the scraper from looking for files.

    The old code gated the whole download phase on the badge, so an unreadable
    one meant "0 documents" and the attachments were never even looked for.
    """
    driver = FakeDriver(TWO, badge=None)
    links, badge = extract_document_links(driver, "run", "REF-1")
    assert badge is None
    assert len(links) == 2, links


def test_falls_back_to_the_tab_url_when_the_click_does_nothing():
    driver = FakeDriver(TWO, badge="2", clickable=False)
    links, _ = extract_document_links(driver, "run", "REF-1", timeout=3)
    assert len(links) == 2, links
    assert driver.gets and "innerTabId=docs-items" in driver.gets[0], driver.gets


def test_a_partial_pre_render_is_not_mistaken_for_the_whole_list():
    """Some bids carry one anchor before the tab is opened and two after.

    Trusting that pre-click scan would download 1 of 2 documents — the same
    class of loss as counting 0 of 2, just quieter.
    """

    class PartialPreRender(FakeDriver):
        def _visible_anchors(self):
            # one anchor is on the page from the start; the rest arrive with the tab
            return self._anchors if self.clicked_at is not None else self._anchors[:1]

    driver = PartialPreRender(TWO, badge="2")
    links, _ = extract_document_links(driver, "run", "REF-1")
    assert len(links) == 2, f"partial pre-render undercounted: {links}"


def test_waits_for_the_full_badge_count_before_settling():
    """A list that renders row by row must not be read half-drawn."""

    class Incremental(FakeDriver):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.scans = 0

        def _visible_anchors(self):
            if self.clicked_at is None:
                return []
            self.scans += 1
            # rows trickle in: 1, then 1, then all three
            return self._anchors[:1] if self.scans < 3 else self._anchors

    three = TWO + [("/private/solicitations/123/abstract/docs-items/3/attachment-download", "C.pdf")]
    driver = Incremental(three, badge="3")
    links, _ = extract_document_links(driver, "run", "REF-1", timeout=10)
    assert len(links) == 3, f"read a half-rendered list: {links}"


def test_no_documents_is_still_zero():
    driver = FakeDriver([], badge="0")
    links, badge = extract_document_links(driver, "run", "REF-1", timeout=2)
    assert links == []
    assert badge == "0"


def test_a_zero_badge_bid_is_cheap():
    """A badge that positively reads 0 must not cost the full render wait.

    An *unreadable* badge is the dangerous one; an explicit 0 is trustworthy, so
    a bid with no attachments should not stall the run for 30 seconds nor take
    the extra tab-URL page load.
    """
    driver = FakeDriver([], badge="0")
    started = time.monotonic()
    links, _ = extract_document_links(driver, "run", "REF-1", timeout=30)
    elapsed = time.monotonic() - started
    assert links == []
    assert elapsed < documents.ZERO_BADGE_TIMEOUT + 3, f"zero-doc bid took {elapsed:.1f}s"
    assert not driver.gets, f"took the fallback page load for a 0-document bid: {driver.gets}"


def test_a_zero_badge_that_lies_is_still_caught():
    """If the tab body does hold files despite a 0 badge, they are still found."""
    driver = FakeDriver(TWO, badge="0")
    links, _ = extract_document_links(driver, "run", "REF-1")
    assert len(links) == 2, links


def test_duplicate_hrefs_are_collapsed():
    dupes = TWO + [TWO[0]]
    driver = FakeDriver(dupes, badge="2")
    links, _ = extract_document_links(driver, "run", "REF-1")
    assert len(links) == 2, links


# -- filenames --------------------------------------------------------------


class _Resp:
    def __init__(self, disposition):
        self.headers = {"Content-Disposition": disposition}


def test_filename_prefers_content_disposition():
    assert filename_from_response(_Resp('attachment; filename="Addendum 1.pdf"'), "x") == "Addendum 1.pdf"


def test_filename_handles_rfc5987():
    got = filename_from_response(_Resp("attachment; filename*=UTF-8''Sch%C3%A9ma.pdf"), "x")
    assert got == "Schéma.pdf", got


def test_filename_falls_back_to_link_text():
    assert filename_from_response(_Resp(""), "Exhibit B.xlsx") == "Exhibit B.xlsx"


def test_fallback_name_sanitises_and_uses_the_url_when_text_is_empty():
    link = DocumentLink(url="https://x/a/b/Plan%20Set.pdf", label="")
    assert link.fallback_name == "Plan Set.pdf", link.fallback_name
    assert "/" not in DocumentLink(url="https://x/1", label="a/b.pdf").fallback_name


# -- downloading ------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """A stand-in for the portal's attachment endpoint.

    Bodies differ per URL, as real attachments do, so only the routes that
    deliberately serve identical bytes exercise the de-duplication:

      /ok/<n>, /vary   distinct documents (equal size, different content)
      /slow/<n>        distinct, large, and slow to arrive
      /twin/<x>        every /twin URL returns the *same* bytes — the portal
                       listing one file under two document ids
      /collide/<n>     distinct bytes, but all share one filename
      /fail            HTTP 500
    """

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path
        if path.startswith("/fail"):
            self.send_response(500)
            self.end_headers()
            return

        size = 2048
        if path.startswith("/slow"):
            size = 300_000
            time.sleep(0.4)

        if path.startswith("/twin"):
            seed, name = b"TWIN", "twin.pdf"
        elif path.startswith("/collide"):
            # unique content, one shared filename — the reservation race
            seed, name = path.encode(), "same.pdf"
            time.sleep(0.1)
        else:
            seed = path.encode()
            # ?fn= lets a test say what Content-Disposition should come back.
            # On the real portal the anchor text *is* the filename the server
            # sends, so tests that care about that match set them to agree.
            query = parse_qs(urlparse(path).query)
            name = (
                query["fn"][0]
                if "fn" in query
                else urlparse(path).path.strip("/").replace("/", "_") + ".pdf"
            )

        filler = (seed * size)[: size - 8]
        body = b"%PDF-1.4" + filler
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        self.wfile.write(body)


def _server():
    # Threading, so the parallel-download test measures the downloader's
    # concurrency rather than the stub server's one-request-at-a-time queue.
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_port}"


def test_downloads_every_detected_document():
    httpd, base = _server()
    try:
        links = [DocumentLink(f"{base}/ok/{i}", f"doc{i}.pdf") for i in range(5)]
        with TemporaryDirectory() as tmp:
            out = download_documents(requests.Session(), links, Path(tmp), "run", "REF-1")
            assert out.detected == 5
            assert out.downloaded == 5, out.failed
            assert not out.failed
            files = sorted(p.name for p in Path(tmp).iterdir())
            assert len(files) == 5, files
            assert all(p.stat().st_size == 2048 for p in Path(tmp).iterdir())
            # no .part left behind
            assert not any(p.suffix == ".part" for p in Path(tmp).iterdir())
    finally:
        httpd.shutdown()


def test_downloads_run_in_parallel():
    """Four 0.4s files must finish in well under the 1.6s a serial loop takes."""
    httpd, base = _server()
    try:
        links = [DocumentLink(f"{base}/slow/{i}", f"big{i}.pdf") for i in range(4)]
        with TemporaryDirectory() as tmp:
            out = download_documents(requests.Session(), links, Path(tmp), "run", "REF-1")
            assert out.downloaded == 4, out.failed
            assert out.seconds < 1.2, f"downloads appear serial: {out.seconds:.2f}s"
    finally:
        httpd.shutdown()


def test_a_failed_file_is_reported_not_swallowed():
    httpd, base = _server()
    try:
        links = [DocumentLink(f"{base}/ok/1", "good.pdf"), DocumentLink(f"{base}/fail", "bad.pdf")]
        with TemporaryDirectory() as tmp:
            # keep the test quick — the retry backoff is what we're skipping
            original = documents.DOWNLOAD_ATTEMPTS
            documents.DOWNLOAD_ATTEMPTS = 1
            try:
                out = download_documents(requests.Session(), links, Path(tmp), "run", "REF-1")
            finally:
                documents.DOWNLOAD_ATTEMPTS = original
            assert out.detected == 2
            assert out.downloaded == 1
            assert len(out.failed) == 1 and "bad.pdf" in out.failed[0], out.failed
            assert not any(p.suffix == ".part" for p in Path(tmp).iterdir())
    finally:
        httpd.shutdown()


def test_same_named_files_downloaded_in_parallel_do_not_collide():
    """BidNet reuses filenames across a bid's attachments.

    Fetched concurrently, two threads must not claim the same path and
    interleave two documents into one corrupt file.
    """
    httpd, base = _server()
    try:
        # distinct content, identical server-supplied filename
        links = [DocumentLink(f"{base}/collide/{i}", "same.pdf") for i in range(6)]
        with TemporaryDirectory() as tmp:
            out = download_documents(
                requests.Session(), links, Path(tmp), "run", "REF-1", max_parallel=6
            )
            assert out.downloaded == 6, out.failed
            files = sorted(Path(tmp).iterdir())
            assert len(files) == 6, [f.name for f in files]
            assert len({f.name for f in files}) == 6, [f.name for f in files]
            # every file is whole — a collision shows up as a wrong size
            for f in files:
                assert f.stat().st_size == 2048, f"{f.name} is {f.stat().st_size} bytes"
                assert f.open("rb").read(4) == b"%PDF", f.name
    finally:
        httpd.shutdown()


def test_byte_identical_copies_are_collapsed():
    """A solicitation lists the same file under two document ids.

    Detection keeps both (scoping it to one table would lose an attachment that
    only appears in the other), so the copy has to be resolved by content —
    otherwise the bid folder holds the same PDF twice and its document count is
    one too high.
    """
    httpd, base = _server()
    try:
        links = [
            DocumentLink(f"{base}/twin/560", "Vendor Certification.pdf"),
            DocumentLink(f"{base}/twin/561", "VENDOR'S CERTIFICATION.pdf"),  # same bytes
            DocumentLink(f"{base}/ok/2", "Scope of Work.pdf"),               # different
        ]
        with TemporaryDirectory() as tmp:
            out = download_documents(requests.Session(), links, Path(tmp), "run", "REF-1")
            assert out.detected == 3
            assert out.duplicates == 1, f"duplicate not collapsed: {out}"
            assert out.distinct == 2, out.distinct
            assert out.downloaded == 2, out.saved
            files = sorted(p.name for p in Path(tmp).iterdir())
            assert len(files) == 2, files
    finally:
        httpd.shutdown()


def test_different_files_of_the_same_size_are_both_kept():
    """Same size is not same content — the hash has to decide, not the size."""
    httpd, base = _server()
    try:
        # /ok/1 and /dup are both 2048 bytes; make one differ in content
        links = [DocumentLink(f"{base}/ok/1", "a.pdf"), DocumentLink(f"{base}/vary", "b.pdf")]
        with TemporaryDirectory() as tmp:
            out = download_documents(requests.Session(), links, Path(tmp), "run", "REF-1")
            sizes = {p.stat().st_size for p in Path(tmp).iterdir()}
            assert len(sizes) == 1, f"test premise broken, sizes differ: {sizes}"
            assert out.duplicates == 0, "distinct files of equal size were collapsed"
            assert out.downloaded == 2, out.saved
    finally:
        httpd.shutdown()


# -- one file per document, however many times the bid is scraped -----------
#
# The bid folder lives for the whole session (see storage.py), so a niche
# re-run lands on a folder that already holds the documents. That used to
# re-download every one and park it beside the original as "… (2).pdf": a
# seven-document bid became 14 files on the second run and 21 on the third.


def _counting_server():
    """A server that also reports how many requests it served."""
    hits = {"n": 0}
    outer = _Handler

    class Counting(outer):
        def do_GET(self):
            hits["n"] += 1
            outer.do_GET(self)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Counting)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_port}", hits


def test_rescraping_a_bid_does_not_duplicate_its_documents():
    """The reported bug, end to end: 7 documents must stay 7 files."""
    httpd, base, hits = _counting_server()
    try:
        links = [DocumentLink(f"{base}/ok/{i}", f"doc{i}.pdf") for i in range(7)]
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "0001 - Some Bid"
            for run in (1, 2, 3):
                out = download_documents(requests.Session(), links, folder, "run", "BID-1")
                files = [p for p in folder.iterdir() if p.is_file()]
                assert len(files) == 7, f"run {run}: {len(files)} files — {sorted(p.name for p in files)}"
                assert out.distinct == 7
                assert out.downloaded == 7, out.saved
            # no "(2)" / "(3)" copies anywhere
            assert not [p.name for p in folder.iterdir() if "(" in p.name]
    finally:
        httpd.shutdown()


def test_a_rerun_makes_no_http_requests_at_all():
    """Already-present files are skipped, not re-fetched and then discarded."""
    httpd, base, hits = _counting_server()
    try:
        # ?fn= makes the server send back the name the anchor advertises, which
        # is how the real portal behaves — and what lets the name check answer
        # a re-run without touching the network.
        links = [
            DocumentLink(f"{base}/ok/{i}?fn=doc{i}.pdf", f"doc{i}.pdf") for i in range(4)
        ]
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "bid"
            download_documents(requests.Session(), links, folder, "run", "BID-1")
            assert hits["n"] == 4, hits
            out = download_documents(requests.Session(), links, folder, "run", "BID-1")
            assert hits["n"] == 4, f"re-downloaded on the second pass: {hits['n']} requests"
            assert out.skipped_existing == 4
            assert out.downloaded == 4, out.saved
    finally:
        httpd.shutdown()


def test_a_repeated_url_in_the_list_is_collapsed_before_downloading():
    httpd, base, hits = _counting_server()
    try:
        link = DocumentLink(f"{base}/ok/1", "doc.pdf")
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "bid"
            out = download_documents(
                requests.Session(), [link, link, link], folder, "run", "BID-1"
            )
            assert out.duplicate_links == 2
            assert out.distinct == 1
            assert hits["n"] == 1, f"fetched a repeated link {hits['n']} times"
            assert len([p for p in folder.iterdir() if p.is_file()]) == 1
    finally:
        httpd.shutdown()


def test_same_bytes_under_a_different_name_are_stored_once_on_every_run():
    """The two-document-id case, held across a re-run.

    The portal lists one file under two document ids, so both links are in
    *every* scrape of the bid — the second run sees the pair again, not one
    half of it. Both runs must report one distinct document and leave one file.
    """
    httpd, base, _ = _counting_server()
    try:
        links = [
            DocumentLink(f"{base}/twin/560", "Vendor Certification.pdf"),
            DocumentLink(f"{base}/twin/561", "VENDOR'S CERTIFICATION.pdf"),
        ]
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "bid"
            for run in (1, 2, 3):
                out = download_documents(requests.Session(), links, folder, "run", "BID-1")
                assert out.duplicates == 1, f"run {run}: {out}"
                assert out.distinct == 1, f"run {run}: {out}"
                # The saved list is what the report speaks from, so it must not
                # outgrow the folder either.
                assert len(out.saved) == 1, f"run {run}: {out.saved}"
                files = [p for p in folder.iterdir() if p.is_file()]
                assert len(files) == 1, f"run {run}: {sorted(p.name for p in files)}"
    finally:
        httpd.shutdown()


def test_two_distinct_documents_sharing_a_name_survive_a_rerun():
    """The name check must not collapse a real second document.

    BidNet does reuse a filename across a bid's attachments, so the folder ends
    up holding "same.pdf" and "same (2).pdf" — both links advertising the same
    name. Answering *both* from disk on the next run would drop one document
    from the report; only the first link may be, and the second has to be
    fetched and judged on its bytes.
    """
    httpd, base, _ = _counting_server()
    try:
        links = [DocumentLink(f"{base}/collide/{i}", "same.pdf") for i in range(2)]
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "bid"
            for run in (1, 2):
                out = download_documents(requests.Session(), links, folder, "run", "BID-1")
                assert out.distinct == 2, f"run {run}: {out}"
                assert out.duplicates == 0, f"run {run}: distinct content was discarded — {out}"
                assert len(out.saved) == 2, f"run {run}: {out.saved}"
                files = sorted(p.name for p in folder.iterdir() if p.is_file())
                assert files == ["same (2).pdf", "same.pdf"], f"run {run}: {files}"
    finally:
        httpd.shutdown()


def test_a_genuinely_different_file_with_a_taken_name_still_gets_stored():
    """Dedup must not swallow distinct content that happens to share a name."""
    httpd, base, _ = _counting_server()
    try:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "bid"
            download_documents(
                requests.Session(), [DocumentLink(f"{base}/ok/1", "same.pdf")],
                folder, "run", "BID-1",
            )
            out = download_documents(
                requests.Session(), [DocumentLink(f"{base}/vary", "same.pdf")],
                folder, "run", "BID-1",
            )
            names = sorted(p.name for p in folder.iterdir() if p.is_file())
            assert len(names) == 2, names
            assert out.duplicates == 0, "distinct content was wrongly discarded"
    finally:
        httpd.shutdown()


def test_an_interrupted_part_file_is_never_counted_as_stored():
    httpd, base, _ = _counting_server()
    try:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "bid"
            folder.mkdir(parents=True)
            (folder / "doc0.pdf.part").write_bytes(b"half a file")
            out = download_documents(
                requests.Session(), [DocumentLink(f"{base}/ok/0?fn=doc0.pdf", "doc0.pdf")],
                folder, "run", "BID-1",
            )
            assert out.skipped_existing == 0, "a .part file was mistaken for a stored document"
            assert (folder / "doc0.pdf").is_file()
    finally:
        httpd.shutdown()


def test_empty_link_list_is_a_no_op():
    with TemporaryDirectory() as tmp:
        out = download_documents(requests.Session(), [], Path(tmp), "run", "REF-1")
        assert out.detected == 0 and out.downloaded == 0 and not out.failed


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:
            print(f"FAIL  {name}: {exc}")
            failures += 1
        except Exception as exc:  # noqa: BLE001 — report, don't abort the suite
            print(f"ERROR {name}: {exc.__class__.__name__}: {exc}")
            failures += 1
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
