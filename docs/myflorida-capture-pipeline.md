# MyFloridaMarketPlace — human OTP login, unfiltered capture, packaged archive

Three changes to the MFMP flows, and the reasoning behind each.

| Layer | File |
| --- | --- |
| Login, search, per-bid crawl (both flows) | `server/app/scrapers/myflorida/scraper.py` |
| The two vendor logins | `server/app/scrapers/myflorida/accounts.py` |
| Ad-status sweep (subclass) | `server/app/scrapers/myflorida/sweep/scraper.py` |
| Output layout | `server/app/scrapers/myflorida/storage.py` |
| Summary sheet | `server/app/scrapers/myflorida/workbook.py` |
| Capture-only persistence | `server/app/scrapers/myflorida/sweep/export.py` |
| Packaging | `server/app/core/exports.py` |
| UI | `client/src/components/MyFloridaPanel.tsx`, `RunStatus.tsx`, `MyFloridaSweepResults.tsx` |

## 1. The login waits for a person

MFMP answers a correct email/password with a one-time password sent to the
account. Nothing on this side can produce that code, so:

* **The browser is visible.** `start_driver(headless=False)` whenever
  `mfmp_manual_otp` is on — not left to the per-run "Live preview" flag, because
  a run started without it would sit at the challenge until the window expired
  with nothing to type into.
* **The wait is the OTP window, not the element timeout.**
  `mfmp_otp_wait_seconds` (default 120) is how long the run gives a human to
  fetch the code. Set both in `server/.env`.
* **The prompt reaches the run, not just the log.** The step becomes
  `awaiting_otp`, `awaiting_otp: true` goes on the run record, and the console
  raises a banner saying what to do. Nobody is reading the server's stdout.

The verification is the part worth keeping straight. The old check was "the URL
no longer says `/login`", which the OTP challenge satisfies the moment the code
is *demanded* — so a half-authenticated session was handed to the search step,
which then failed somewhere unrelated with a message about a missing button.
`_authenticated` waits for the dashboard: off `/login` **and** the Advanced
Search button (or the signed-in shell) on screen.

With `mfmp_manual_otp` off, the wait is the ordinary login timeout and an
account that *is* challenged fails saying exactly that, rather than hanging.

## 2. The evaluation matrix is out of the pipeline

The sweep used to run every advertisement through a C/T/S scoring matrix, file
it into a niche lane, and **delete its attachments** once their text had fed the
score. Two consequences: what reached the reviewer was the classifier's opinion
of the portal, and the documents behind that opinion were already gone.

Now: every ad the search returns is captured as it stands, its attachments kept,
and the reviewer decides. Concretely —

* `sweep/scraper.py` imports no classifier. `_visit_bid` became `_capture_bid`
  and keeps what it downloads; `_classify` became `_record`, which merges the
  portal's export columns with what the detail page gave and interprets nothing.
* `sweep/export.py` gained `save_capture()`, which writes the bid row and stops:
  no per-niche score rows, `primary_niche = "UNREVIEWED"` — a placeholder in a
  NOT NULL column, not a verdict.
* The niche/keyword flow lost its close-date filter. It pruned the workbook to
  ads at least `MIN_DAYS_UNTIL_CLOSE` days from closing, which silently removed
  bids a reviewer might still have wanted; `filter_workbook_by_close_date` is
  gone with it. `app.core.closing_filter` still serves the portals that use it.

**The classifier code is still in the tree** — `scoring.py`, `routing.py`,
`matching.py`, `config.py`, `mfmp_niches.yaml`, its test suite, the
`/myflorida/sweep/niches` endpoints and the lane UI. It serves sweep runs
recorded *before* this change, which still open in the console exactly as they
did. Nothing in the scraping path calls it. `export.generate_excel` decides which
workbook to rebuild from the data itself: a run whose bids are all `UNREVIEWED`
gets the flat summary sheet, anything else gets its lane workbook.

## 3. One archive, with a shape

Both flows build the same tree inside the run workspace, and the ZIP is that
tree:

```
MyFlorida_Export/
├── MyFlorida_Bids_Summary.xlsx        every bid, one row, with its folder named
└── Bids_Data/
    ├── DMS-21-22-001/
    │   ├── Solicitation.pdf
    │   └── Specs.docx
    └── RFP-2026-114/
        └── Attachment.pdf
```

* **The summary is the index.** Every column the portal's own export carries,
  plus `Niche`, `Matched Keyword` and `Folder` — and `Folder` holds a path
  *inside the archive* (`Bids_Data/DMS-21-22-001`), not an absolute server path
  the reader has no access to. No row is dropped.
* **Folders are named for the ad number alone.** It is the only part of a bid
  that is unique, short, stable and quotable back at the portal; titles are long,
  truncate differently in different places, and two ads can share one. The title
  is in the sheet, where it is searchable.
* **The root folder is inside the workspace**, so the ZIP unpacks to a single
  `MyFlorida_Export/` instead of scattering files into the current directory.
* `_exports/` (the raw per-search workbooks the summary is stitched from) and
  `_downloads/` (Chrome's staging) sit outside that root, which is what keeps
  them out of the archive.

The sweep left `EXCEL_ONLY_PORTALS` as part of this: it was there because it
deleted its attachments and the sheet was the whole deliverable. It keeps them
now, so it ships the same ZIP the niche flow does.

One ordering note in `_finalize`: the summary is written to disk **first and
always**, before the DB save. The old sweep leaned on the database to rebuild its
workbook on demand; the deliverable is now an archive whose root holds the sheet
next to the documents it indexes, so the file has to exist at packaging time
whatever the database is doing. A DB failure sets `db_save_failed`, which tells
the download path to serve the sheet on disk rather than regenerate an empty one.

## Two accounts

A run signs in as one of two vendor logins — **Hoope Lab** or **Auston Lucas** —
chosen in the console. Both see the same catalogue of advertisements, unlike the
RideMetro switch where the login decides which supplier network gets swept, so
the choice is about *whose* registration does the searching and which inbox the
one-time password lands in. Both flows share it, because a sweep signs in
through the same form.

**Accounts are named for their client, not for their position.** "Account 1"
tells the person at the dashboard nothing — they know whose bids they are after,
not which slot in a config file it occupies — and it goes stale the moment a
third is added or the order changes. Hoope Lab is spelled the way
`ridemetro/accounts.py` already spells it, so one client reads the same way on
both portals. The `.env` keys stay `MYFLORIDA_ACC1_*`/`ACC2_*`: they are what is
deployed, and renaming a key holding a working credential buys nothing. The old
`account_1`/`account_2` values still resolve, so a saved link keeps working.

`accounts.py` mirrors `app/scrapers/ridemetro/accounts.py` deliberately: two
portals with an account switch should not have two different ideas of what an
account is.

**The account is checked before the run is created.** That gate earns more here
than anywhere else in the codebase: an MFMP run opens a *visible* browser and
stops at an OTP challenge for a person to type into. A run started with
credentials that were never going to work does not just waste a process — it
wastes somebody sitting in front of it waiting to type a code. An unconfigured
account is a 503 on the button naming the `.env` keys to set, and the picker
shows it as unavailable rather than offering it.

**Nothing about a credential reaches the screen.** The catalog the picker is
built from carries a label and a `configured` flag, never a username. The
address appears once, in the run log, masked to `ac…@example.com` — enough to
know which account signed in, not enough to be the address. The MFMP run log is
streamed to the dashboard, so a full address there would be on screen.

```
[JOB INITIALIZED]: Portal: MyFloridaMarketPlace (MFMP)
 ├── [ACCOUNT SELECTED]: Auston Lucas (au…)
 ├── [LAUNCHING BROWSER]: Headed mode for manual OTP verification...
 └── [AUTHENTICATION]: Injecting Auston Lucas credentials into login form...
```

## Settings

```ini
# server/.env
MFMP_MANUAL_OTP=true        # visible browser + wait for a human at the OTP
MFMP_OTP_WAIT_SECONDS=120   # how long to wait before giving up

# The two vendor logins: ACC1 is Hoope Lab, ACC2 is Auston Lucas. An account
# with either key blank is shown as unavailable in the picker.
MYFLORIDA_ACC1_USERNAME=...
MYFLORIDA_ACC1_PASSWORD=...
MYFLORIDA_ACC2_USERNAME=...
MYFLORIDA_ACC2_PASSWORD=...

# Hoope Lab falls back to these when MYFLORIDA_ACC1_* are unset, so a
# deployment that predates the switch keeps running untouched.
MFMP_EMAIL=...
MFMP_PASSWORD=...
```
