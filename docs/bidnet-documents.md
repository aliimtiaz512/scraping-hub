# BidNet Direct — attachment detection and download

> Bids gated behind a "required acknowledgement" page are covered in
> [bidnet-acknowledgements.md](bidnet-acknowledgements.md) — those never reach
> the document phase at all.


Why bids reported "0 documents" when they had files, and how the download phase
got faster. Code lives in `server/app/scrapers/bidnet/documents.py`; tests in
`server/tests/test_bidnet_documents.py`.

## What the page actually does

Verified against live solicitations. A bid's attachments look like this:

```html
<a class="mets-command-link"
   id="attachmentDownloadLnk_9418316697"
   href="/private/solicitations/9419210303/abstract/docs-items/9418316697/attachment-download">
   RTA RFP 2026-008 QLINE Custodial Cleaning Services.pdf</a>
```

Three properties drive the whole design:

1. **The anchors are lazy.** Until the Documents tab is clicked they are *not in
   the DOM at all*. On 22 live bids, 21 had zero `attachmentDownloadLnk` anchors
   before the click and the full set after. A page can also render *part* of the
   list early — one bid had one anchor present and two after — so a non-empty
   pre-click scan is not proof the list is complete.
2. **The hrefs are real, direct URLs.** They are ordinary authenticated GETs, so
   once the browser's cookies are copied into a `requests` session the files can
   be fetched without the browser at all.
3. **The count badge is not authoritative.** It is usually right, but a
   solicitation lists the same file in both the documents table and the
   line-items table under two different document ids, so the anchor count can
   exceed it.

Attachments are **not** inside iframes — the only frames on a solicitation page
are third-party (ShareThis, Drift chat). No frame traversal is needed.

## The false-zero bug

Two independent causes, both fixed:

| Cause | Old behaviour | Now |
| --- | --- | --- |
| Download gated on the count badge | `_document_count()` returned `"0"` on *any* failure to read `.tabCount` — a slow render, a `WebDriverException`, an `innerText` of `""` from the CSS-hidden mobile/desktop tab twin. `process_bid` then skipped the download phase entirely. | The badge is never a gate. The tab is always opened and the **anchors themselves** are waited for. The badge is read with `textContent` (immune to hidden elements) and used only as a cross-check to log against. |
| Fixed sleep after the tab click | `time.sleep(5)`, then take whatever is in the DOM. A tab whose AJAX took longer yielded nothing, and the bid recorded 0 documents despite a badge saying otherwise. | An explicit wait on the anchors appearing (up to 30s), holding out for the badge's count when known, then a settle loop until the count stops growing. A bid whose badge positively reads `0` takes a short 4s confirmation instead. |

A fallback covers a tab whose JS never fires at all: load
`?innerTabId=docs-items`, which renders the same list server-side.

## Downloads

Direct HTTP instead of browser clicks:

- `build_session` copies the browser's cookies and UA into one pooled
  `requests.Session` per run, cookies refreshed per bid (BidNet rotates the
  session cookie; a stale one turns a download into a login-page redirect).
- Up to **4 attachments per bid download concurrently**.
- Each file **streams to disk** in 256 KB chunks, so a large drawing set never
  sits in memory.
- Each writes to a `.part` file first, renamed on success, so an interrupted
  transfer never leaves a truncated file looking complete. Filename reservation
  is under a lock — the portal reuses filenames across a bid's attachments, and
  without it two threads open the same `.part` and interleave two documents into
  one corrupt file.
- `Content-Disposition` supplies the filename (RFC 5987 `filename*` included),
  falling back to the anchor text, which on BidNet *is* the filename.
- 3 attempts with exponential backoff; a short read against a declared
  `Content-Length` counts as a failure rather than a file.

### De-duplication

Detection deliberately keeps every anchor — scoping it to the documents table
would lose an attachment that only ever appears in the items table. The genuine
duplicates (same bytes, two document ids, two display names) are resolved
**after** download by content: files are grouped by size, and only same-size
candidates are hashed. Byte-identical copies are removed and counted, so the
badge reconciliation compares against the *distinct* count.

## Logging

Per bid:

```
Found 2 documents for BID-123 | Downloaded 2/2 in 1.4s
```

Detected-but-not-downloaded is never silent — it warns per bid and adds a run
error. A badge claiming files where no link could be found logs an error and
screenshots the page. Per run:

```
[DOCUMENTS] run <id> | Detected: 61 | Downloaded: 61 | Failed: 0 | Duplicate copies removed: 1 | Count mismatches: 0
```

These also land on the run record as `documents_detected` /
`documents_downloaded` / `documents_failed`.

## Measured on live BidNet

- **61/61 documents downloaded across 12 bids (100%)** — 0 incomplete bids, 0
  corrupt files, 43.6 MB.
- Detection: **~1.2s per bid**, replacing the old flat 5s sleep.
- Download phase, same bids and files, serial vs parallel HTTP: **1.49x**
  (155.6s → 104.4s over 41 documents). That compares parallel HTTP against
  *serial HTTP*; the path it replaced was serial browser click-and-wait, each
  file polling Chrome's download directory, so the real-world gain is larger.
