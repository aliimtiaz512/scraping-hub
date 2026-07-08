# BidNet Direct Bid Scraper — Integration Plan

Third scraper for the scraping-hub project, added alongside the existing
MyFloridaMarketPlace (MFMP) and RideMetro (Bonfire) scrapers. The working
scraper already lives in the top-level **`backend/`** folder (a standalone
FastAPI + Playwright app). This plan **integrates it into the `server/`
microservice** without changing its scraping behaviour, adds a database table
for it, and adds one Excel change.

Portal: BidNet Direct (`https://www.bidnetdirect.com`).

## 1. What I understand from the requirements

1. Take the existing BidNet Direct scraper currently in `backend/` and **move it
   into `server/`** as its own module, following the same "one package per
   portal" microservice structure the other two scrapers already use
   (`app/scrapers/<portal>/`). The BidNet codebase must be **easy to find**
   (self-contained under `app/scrapers/bidnet/`).
2. **Keep the same BidNet scraping flow** — login, search, "Member Agency Bids"
   filter, pagination, per-bid detail extraction, and document downloads stay
   exactly as they are today in `backend/scraper.py` (same steps, same selectors,
   same field set, same folder layout). The **one change is the browser engine:
   port it from Playwright to Selenium** so BidNet matches the other two scrapers
   and reuses the shared `core/base_scraper.py`.
3. **Add a database table** for this scraper (in the shared `server` database,
   alongside the MFMP and RideMetro tables).
4. **Excel change (the one minor change):** today the Excel is produced only
   on-demand via an "export Excel" button (`GET /api/export`, writing
   `bids_export.xlsx` to the working directory). Keep that, **but also** — **when
   a run completes** — automatically write the Excel to the **BidNet Direct root
   folder**, named **`Document_Bids_BidnetDirect (<date> <time>)`** where the
   date and time are **when the scraper ran** (e.g.
   `Document_Bids_BidnetDirect (2026-07-08 14-30-05).xlsx`). Everything else is
   the same as the existing `server` logic (background runs, run status, `/bids`
   listing, DB persistence).

### Consistency with the other two scrapers

- MFMP and RideMetro use **Selenium (synchronous)** built on
  `core/base_scraper.py`. BidNet is currently **Playwright (async)** in
  `backend/`. Per your instruction, **BidNet is ported to Selenium** and
  subclasses the same `BaseScraper` (Chrome driver, per-run staging download
  dir, `wait_for_download`, screenshots, step/status reporting). All three
  scrapers then share one engine and one set of conventions — no Playwright
  anywhere.
- Porting keeps the **flow identical**; only Playwright-specific constructs are
  translated to their Selenium equivalents (see §6.1). The observable behaviour —
  which pages are visited, which elements are clicked, which fields are read,
  where files land — is unchanged.

## 2. Structure — BidNet as its own module

Target: a new self-contained package `app/scrapers/bidnet/`, mirroring how
`myflorida/` and `ridemetro/` are laid out.

```
server/
├── main.py                         # mounts myflorida + ridemetro + bidnet routers
├── create_tables.py                # message updated to list bidnet tables too
├── requirements.txt                # unchanged for Selenium (optionally + pandas/requests, see §9)
├── .env / .env.example             # + BIDNET_* credentials
└── app/
    ├── config.py                   # + BidNet settings (link, username, password)
    ├── db.py                       # init_db() also imports bidnet models
    ├── core/                       # SHARED (unchanged) — Selenium base, run_manager, filenames
    └── scrapers/
        ├── myflorida/              # (unchanged)
        ├── ridemetro/              # (unchanged)
        └── bidnet/                 # NEW — self-contained BidNet package
            ├── __init__.py
            ├── router.py           # /bidnet/* endpoints
            ├── scraper.py          # BidnetScraper(BaseScraper) — Selenium port of backend flow + execute_run()
            ├── models.py           # BidnetRun, BidnetBid + EXCEL_COLUMNS
            └── export.py           # save_bid / save_run / generate_excel (DB -> xlsx)
```

### Mapping from `backend/` to the new module

| From (current `backend/`) | To (new) | Change |
|---|---|---|
| `backend/scraper.py` (Playwright/async) | `app/scrapers/bidnet/scraper.py` (Selenium/sync) | **same flow & selectors**, translated to Selenium on `BaseScraper`; reads creds from shared config; gains `execute_run()` + progress/DB hooks |
| `backend/models.py` (`Bid`, table `bids`) | `app/scrapers/bidnet/models.py` (`BidnetBid`, table `bidnet_bids` + new `BidnetRun`) | re-parent onto shared `app.db.Base`; rename table to avoid collisions; add `run_id` |
| `backend/main.py` (`/api/scrape`, `/api/bids`, `/api/export`) | `app/scrapers/bidnet/router.py` (`/bidnet/*`) | reshaped to the server's background-run + run_manager convention |
| `backend/database.py` | — (dropped) | replaced by shared `app/db.py` |
| Excel via `pandas` on-demand | `app/scrapers/bidnet/export.py` | on-demand kept; **plus** auto-write on run completion |

**Decided:** the original top-level `backend/` folder is **deleted once the
integrated `/bidnet/*` scraper is verified working end-to-end** (step 8). The
helper/debug scripts (`inspect_buttons.py`, `investigate_docs.py`, `test_*.py`,
`*.png`) are **not** migrated; they were one-off DOM-investigation tools and go
away with the folder.

## 3. Environment & config

BidNet's current `.env` uses generic keys (`USERNAME`, `PASSWORD`) which are
**unsafe in a shared `.env`** (`USERNAME` collides with an OS environment
variable on many systems). Rename them to `BIDNET_*` and add to
**`server/.env`** and **`server/.env.example`**:

```
# BidNet Direct
BIDNET_DIRECT_LINK=https://www.bidnetdirect.com
BIDNET_USERNAME=your_email
BIDNET_PASSWORD=your_password
```

`DATABASE_URL` and `DOWNLOAD_DIR` are already shared in `server/.env`. In
`app/config.py`, add:

```python
# BidNet Direct
bidnet_direct_link: str = "https://www.bidnetdirect.com"
bidnet_username: str = ""
bidnet_password: str = ""
```

The scraper reads these three values from `settings` instead of `os.getenv`
(the only edit to how it obtains input — the automation steps are untouched).

## 4. Output folders & the Excel change

BidNet's existing download logic builds a **per-keyword run folder** and, inside
it, one `"<reference> - <title>"` folder per bid holding that bid's downloaded
documents. That folder layout is **preserved**, rooted under the shared documents
directory:

```
server/documents/
├── Document_Bids_BidnetDirect_AI/                 # bidnet's per-keyword download folder (unchanged logic)
│   ├── 0000-123 - Some Bid Title/
│   │   ├── spec.pdf
│   │   └── addendum.pdf
│   └── 0000-456 - Another Bid/
│       └── notice.pdf
└── Document_Bids_BidnetDirect (2026-07-08 14-30-05).xlsx   # NEW: written at run completion
```

- The **document downloads** keep BidNet's current keyword-based folder naming
  (`<DOWNLOAD_DIR>/Document_Bids_BidnetDirect_<keyword>`). Only the base path
  changes so it lands under `server/documents/` (the shared root the other
  scrapers use). This preserves "all logic is the same".
- The **NEW Excel** is written **once, when the run completes**, to the **BidNet
  root folder** (`server/documents/`, the parent of the download folders), named
  `Document_Bids_BidnetDirect (<date> <time>).xlsx`, where `<date> <time>` is the
  run's start time formatted with the shared `timestamp()` helper
  (`%Y-%m-%d %H-%M-%S`, no colons — filesystem-safe). It is generated from the
  DB (this run's rows) using the same columns the on-demand export uses.

> **Decided:** "root folder for bidnet direct" = the BidNet documents root
> (`server/documents/`), **not** inside the per-keyword download folder.

The existing on-demand **export button** (`GET /bidnet/export`) is preserved with
its current behaviour (returns an `.xlsx` download), so nothing is lost.

## 5. Database

New tables in the shared `server` database, following the runs+bids pattern the
other scrapers use. Reuses the exact BidNet columns already defined in
`backend/models.py`, adds a `run_id` link and a run table.

**`bidnet_runs`** — one row per run
- `run_id` (PK), `status`, `started_at`, `finished_at`
- `keyword`, `bids_found`, `documents_downloaded`, `excel_path`, `folder`

**`bidnet_bids`** — one row per solicitation (same fields as today)
- `id` (PK), `run_id` (FK -> bidnet_runs)
- `reference_number`, `solicitation_number`, `solicitation_type`, `title`
- `publication_date`, `question_acceptance_deadline`, `closing_date`
- `documents_count`
- `scraped_at`

The table is named **`bidnet_bids`** (not `bids`) to avoid colliding with MFMP's
and RideMetro's bid tables. Unique constraint on `(run_id, reference_number)` so
re-runs upsert instead of duplicating (same posture as the other scrapers).

Both models are imported inside `app/db.py`'s `init_db()` so
`Base.metadata.create_all` creates them alongside the existing tables. The
`create_tables.py` "Done" message is updated to list `bidnet_runs, bidnet_bids`.

## 6. Scraper (`app/scrapers/bidnet/scraper.py`)

`BidnetScraper(BaseScraper)` reimplements the exact `backend/scraper.py` flow in
Selenium. The step sequence and selectors are unchanged:

1. **Login** — `driver.get(bidnet_direct_link)` → click `#header_btnLogin` →
   wait for `#j_username` → fill `#j_username` / `#j_password` → click
   `#loginButton` → wait for `#btnSolicitations` (success signal; screenshot on
   failure, same as today).
2. **Search** — wait for `#solicitationSingleBoxSearch`, fill the keyword, click
   `#topSearchButton`, wait for `.searchContentGroupContainer`.
3. **Member Agency Bids filter** — click
   `div[search-content-group-id='2085061601']`, wait for
   `table tbody tr.mets-table-row`.
4. **Pagination** — collect `tr.mets-table-row a.solicitationsTitleLink` hrefs
   across every results page; advance via
   `a.next.mets-pagination-page-icon:not(.disabled)` (and the same fallbacks);
   stop when there's no enabled next link or the first row stops changing. Same
   dedup and `max_pages` safety guard.
5. **Per bid** — for each collected link, open the detail page, extract
   Reference Number, Solicitation Number, Solicitation Type, Title, Publication
   Date, Question Acceptance Deadline, Closing Date; read the documents count
   from `#docs-itemsAbstractTab a .tabCount`; if non-zero, open the docs tab and
   download each document into `<download folder>/<reference> - <safe title>/`
   (same per-bid folder naming and same download-link filter). Append the same
   record dict to `scraped_data`.

### 6.1 Playwright → Selenium translations (behaviour preserved)

| Playwright (today) | Selenium (port) |
|---|---|
| `page.click(sel)` / `page.fill(sel, v)` | `driver.find_element(...).click()` / `.send_keys(v)` |
| `page.wait_for_selector(sel)` | `WebDriverWait(...).until(EC.presence_of_element_located(...))` |
| `:has-text('Field')` field locator | XPath: `//div[contains(@class,'mets-field')][.//*[contains(normalize-space(),'Field')]]//div[contains(@class,'mets-field-body')]//p` |
| `page.wait_for_load_state('networkidle')` | element-based `WebDriverWait` + the same short `time.sleep()` guards the code already uses |
| `context.new_page()` per bid, then close | navigate the driver directly to each collected link (URLs already gathered) — same pages visited |
| `expect_download` + `save_as` | Chrome download prefs (staging dir) + `BaseScraper.wait_for_download()` → move into the bid folder |
| fallback `context.request.get(href)` | fallback fetch with the driver's cookies (e.g. `requests`/`urllib`) → write bytes to the bid folder |

The only non-mechanical edits:
- Credentials/link come from `settings` (`bidnet_direct_link`,
  `bidnet_username`, `bidnet_password`) instead of `os.getenv`.
- The download base folder roots under the shared documents dir
  (`settings.documents_root`), keeping BidNet's `..._<keyword>` per-run naming.
- The browser launch honours the shared `HEADLESS` flag (inherited from
  `BaseScraper`), like MFMP/RideMetro.

### 6.2 Orchestration — `execute_run(run_id, keyword)`

The background-task entry point (matching `ridemetro.execute_run`):

1. `run_manager.update_run(status="running", step="scraping")`, `start_driver()`.
2. Walk the flow above, appending per-bid results to `run_manager`
   (`add_bid_result`, which maintains `bids_processed`/`documents_downloaded`) —
   one failing bid logs an error and continues, exactly like today's per-bid
   try/except.
3. Persist: upsert each record into `bidnet_bids` (`export.save_bid`), deduped by
   `reference_number` as `backend/main.py` does today.
4. **Generate the Excel** into `server/documents/Document_Bids_BidnetDirect
   (<timestamp>).xlsx` via `export.generate_excel`; record `excel_path`.
5. Mark the run `completed` (or `failed`); always set `finished_at`, save the run
   row, and `cleanup()` the driver.

## 7. Persistence & Excel (`app/scrapers/bidnet/export.py`)

Mirrors `ridemetro/export.py`:

- `save_run(run)` — upsert `bidnet_runs` (keyed by `run_id`).
- `save_bid(run_id, record)` — upsert `bidnet_bids` (keyed by
  `run_id + reference_number`).
- `generate_excel(run_id, out_path)` — query this run's `bidnet_bids` rows and
  write an `.xlsx`. Columns match today's export headers: Reference Number,
  Solicitation Number, Solicitation Type, Title, Publication Date, Question
  Acceptance Deadline, Closing Date, Documents Count.

Excel writer: to keep BidNet's current behaviour, the on-demand export can keep
using **pandas** (as today). The auto-generated run Excel can use the same
`EXCEL_COLUMNS` mapping. (`openpyxl` is already a dependency; `pandas` is added —
see §9. If you'd rather not add pandas, both paths can use `openpyxl` directly
like RideMetro does — noted in Open Questions.)

## 8. API endpoints (`app/scrapers/bidnet/router.py`)

Mounted under `/bidnet`, matching the server's conventions (and reachable via
the existing `NEXT_PUBLIC_API_URL`):

| Endpoint | Method | Purpose |
|---|---|---|
| `/bidnet/scrape` | POST | Body `{ "keyword": "AI" }`. Starts a background run; returns `run_id` + folder |
| `/bidnet/scrape/status/{run_id}` | GET | Live progress: status, step, bids found/processed, docs, errors |
| `/bidnet/scrape/runs` | GET | List past runs |
| `/bidnet/bids` | GET | Stored solicitations (optional `query` search over title/solicitation/reference, like today) |
| `/bidnet/export` | GET | On-demand Excel download (preserves the current export-button behaviour) |

`main.py` gains `app.include_router(bidnet_router)` and lists `bidnet` in the
health payload's `scrapers`.

## 9. Dependencies

**No Playwright.** The Selenium port reuses the server's existing stack —
`selenium`, `webdriver-manager`, `openpyxl`, `sqlalchemy`, `psycopg2-binary` are
already in `server/requirements.txt`. The shared `BaseScraper` already provisions
Chromedriver via `webdriver-manager`, so there is no `playwright install` step.

Add only if needed:
```
pandas     # only if the on-demand export keeps using pandas (see §7); skip if both Excel paths use openpyxl
requests   # only if the document-download fallback uses requests instead of urllib
```

## 10. Frontend (secondary)

Consistent with the existing portal switcher (MyFlorida | RideMetro), add a
**BidNet** panel: a keyword text input + "Start scrape" button, the same live
status panel, and a results table of scraped solicitations. API calls target
`/bidnet/*`. This is a follow-up; the server integration is the priority.

## 11. Implementation order

1. **Package scaffold** — create `app/scrapers/bidnet/` with `__init__.py`.
2. **Config & env** — add `BIDNET_*` to `config.py`, `server/.env`,
   `server/.env.example`.
3. **Models** — `bidnet_runs`, `bidnet_bids`; import them in `db.py`'s
   `init_db()`; update `create_tables.py` message.
4. **Scraper** — port `scrape_bids` to `BidnetScraper(BaseScraper)` (same flow &
   selectors, Selenium equivalents per §6.1); creds from `settings`; root the
   download folder under `server/documents/`; add `execute_run`.
5. **Export** — `save_run`, `save_bid`, `generate_excel`; wire the auto-Excel on
   completion (`Document_Bids_BidnetDirect (<timestamp>).xlsx` at the docs root).
6. **Router** — `/bidnet/*` endpoints; mount in `main.py`; add to health payload.
7. **Deps** — none required for Selenium; add `pandas`/`requests` only if §7/§6.1
   opt into them.
8. **Verify** — `create_tables.py` creates the two new tables; `POST
   /bidnet/scrape {keyword}` runs end-to-end; downloads land under
   `server/documents/`, the timestamped Excel appears at the docs root, and rows
   land in `bidnet_bids`. Confirm MFMP and RideMetro are unaffected.
9. **Frontend (optional)** — BidNet panel in the Next.js dashboard.

## 12. Decisions & remaining assumptions

**Decided with you:**
- **Browser engine** — Selenium (no Playwright); BidNet subclasses the shared
  `BaseScraper`, same flow/selectors as `backend/scraper.py`.
- **Excel location** — the auto-Excel goes in the BidNet documents root
  (`server/documents/`), next to the per-keyword download folders.
- **Old `backend/` folder** — deleted after the integrated scraper is verified
  (step 8).
- **Headless** — BidNet honours the shared `HEADLESS` env flag (inherited from
  `BaseScraper`).

**Remaining assumptions:**
- **System Chrome** — `webdriver-manager` provisions the matching Chromedriver
  automatically; the host still needs a Chrome/Chromium browser installed (same
  requirement the MFMP/RideMetro scrapers already have).
- **Excel writer** — keep `pandas` for the on-demand export (adds a dependency),
  or standardise on `openpyxl` (already installed) for both Excel paths?
- **Keyword source** — BidNet needs a search keyword per run (today it's passed
  to `/api/scrape`). Assumed the frontend/API supplies it; no fixed category
  list like MFMP.
- **Download waiting** — Selenium has no `expect_download`; the port relies on
  Chrome's download prefs + `BaseScraper.wait_for_download()`. If a document
  requires an acknowledgement/terms modal (the code's fallback path), the same
  Escape-and-fallback-fetch behaviour is reproduced.