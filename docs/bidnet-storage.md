# BidNet Direct — session storage and unified packaging

How a day's niche runs collect into one folder tree and ship as one ZIP. Code
in `server/app/scrapers/bidnet/storage.py`; packaging in
`server/app/core/exports.py`; tests in `server/tests/test_bidnet_storage.py`.

## The layout

A run is one niche. A working day is usually several, and they belong together:

```
BidNet_Exports_2026-08-04/          <- session root, one per day
├── IT Services/                    <- one folder per niche actually run
│   ├── IT Services_Bids.xlsx
│   └── documents/
│       ├── REF-101 - Network Refresh/
│       │   ├── spec.pdf
│       │   └── rfp.pdf
│       └── REF-102 - Helpdesk/
│           └── sow.pdf
└── Construction/
    ├── Construction_Bids.xlsx
    └── documents/
        └── REF-201 - Bridge Deck/
            └── drawing.pdf
```

Only niches that ran get a folder — run 2 of the ~5 in the catalog and the root
holds exactly 2. Folders are created as runs happen, never pre-created.

**Documents keep per-bid subfolders.** The brief's example showed files directly
under `documents/`; two solicitations in one niche routinely ship an
"Addendum 1.pdf" apiece, and a flat folder lands the second on top of the first.
The bid folder is `<reference> - <title>`, so attribution survives too.

## Where each piece is decided

| Thing | Where |
| --- | --- |
| Session root (`BidNet_Exports_<date>`) | `storage.session_root()` |
| Niche folder name | `storage.niche_dirname()` — the niche's slug, else its label |
| `documents/` | `storage.documents_folder()` — *not* created until a file lands in it |
| `<Niche>_Bids.xlsx` | `storage.excel_path()` |
| ZIP name | `storage.zip_name()` — the root's own name |
| Run wiring | `router.start_scrape` sets `session_root` + `niche_folder` on the run |
| Packaging | `exports._archive_bidnet` |

`BidnetScraper` reads `run["folder"]` (the niche folder) and puts documents in
`documents/` beneath it. Nothing builds these paths by hand.

## Packaging

`exports.archive_run` routes BidNet to `_archive_bidnet`, which differs from
every other portal in two ways:

1. **It zips the whole session root**, not `run["folder"]`. Zipping the run's
   own folder would ship one niche and silently drop the rest of the day.
2. **It does not delete the workspace.** Every other portal's run ends with
   `_cleanup_workspace`; doing that here would throw away the niches already
   finished. The root is kept and `storage.prune_old_sessions()` removes roots
   older than 3 days.

The archive is `data/archives/BidNet_Exports_<date>.zip`, rebuilt as each niche
finishes, and **every run of that day is pointed at it**. So a download
triggered from the morning's run also carries the afternoon's niches — "all
niches processed prior to the download trigger", without any of them
overwriting another.

Rebuilds go through a temp file and an atomic replace, under a lock, so two
niches finishing at once cannot corrupt the archive and a download in flight
never reads a half-written ZIP.

## Re-running a niche

The niche folder is reused rather than duplicated. Its sheet is rebuilt by
`export.generate_excel_for_runs()` across **every run of that niche in the
session**, de-duplicated by reference number (latest row wins), so the second
run adds to the sheet instead of replacing the first run's results. Documents
accumulate in the same `documents/` tree.

## The sheet-safety rule

`_refresh_niche_excel` regenerates `<Niche>_Bids.xlsx` from the database, but
writes to a `.tmp` first and only swaps it in **when the DB returned rows**.

A run whose DB save failed has its bids only in the sheet the scraper wrote from
memory, at that same path. Regenerating straight over it would replace real
results with an empty workbook at the last step of the run. If the DB comes back
empty and a sheet already exists, the existing one stands and a warning is
logged.

## What did not change

Page navigation, keyword searching, sidebar filters, document detection and
download (`documents.py`), and the evaluation/close-date filtering are all
untouched. This is purely where the resulting files are put and how they are
bundled.
