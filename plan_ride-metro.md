# RideMetro (Bonfire) Bid Scraper — Plan

Second scraper for the scraping-hub project, added alongside the existing
MyFloridaMarketPlace (MFMP) scraper. Portal: RideMetro on Bonfire
(`https://ridemetro.bonfirehub.com/opportunities/174351`).

## 1. What I understand from the requirements

Build a scraper for the RideMetro Bonfire vendor portal that:

1. **Logs in** using RideMetro credentials stored in the server's `.env`.
2. Lands on the **Open Public Opportunities** page — a table of opportunities. The
   last column, **Actions**, has a **"View Opportunity"** button per row.
3. For **each opportunity (one by one)**, clicks **View Opportunity** to open its
   detail page.
4. On the detail page, finds the **Project Details** section and scrapes these
   fields: **Project, Ref. #, Department, Type, Status, Open Date, Intent to Bid
   Due Date, Question Due Date, Contact Information, Close Date, Days Left,
   Project Description**.
5. Scrolls down to the **Supporting Documentation** section and clicks
   **"Download All files"**, which downloads a **zip** of that opportunity's
   documents.
6. Organizes output **per run**:
   - A run folder named **`Document_Bids_RideMetro (<date> <time>)`** where date/time
     are when the scraper ran (e.g. `Document_Bids_RideMetro (2026-07-08 14-30-05)`).
   - Inside it, **one zip per opportunity** (renamed to identify the bid), plus
     **one Excel sheet** containing the scraped Project Details for every bid in
     the run.
7. Stores every opportunity's scraped Project Details in a **new database table**
   for RideMetro.
8. **Generates the Excel from the database** (query this run's rows → write .xlsx)
   and places it in the run folder next to the zips.

### Difference from the MFMP scraper (important)

- MFMP **downloads** an Excel export from the portal. RideMetro has **no export
  button** in this flow — instead we **scrape Project Details into the DB, then
  generate the Excel ourselves from the DB**. So the data path is
  `scrape → DB → Excel` rather than `download Excel → DB`.
- RideMetro documents come as **one zip per opportunity** ("Download All files"),
  not individual file downloads.

## 2. Structure — treat each portal as its own module ("microservice")

**Decided: Option A — modular restructure into `scrapers/<portal>/` packages.**

You asked for RideMetro as a microservice and for both scrapers to be easy to
find on the server side. The current server is a flat layout where everything is
implicitly MFMP:

```
server/app/
├── data/commodity_codes.py     # MFMP
├── models.py                   # MFMP (ScrapeRun, Bid)
├── routes/scraper.py           # MFMP
└── services/
    ├── mfmp_scraper.py         # MFMP
    ├── excel_ingest.py         # MFMP
    └── run_manager.py          # shared-ish
```

**Target structure** — a `scrapers/` package with one self-contained module per
portal, sharing common infrastructure in `core/`:

```
server/
├── main.py                         # FastAPI app; mounts both routers
├── create_tables.py                # imports both scrapers' models, creates all tables
├── requirements.txt
├── .env / .env.example             # MFMP_* and RIDEMETRO_* creds + shared DATABASE_URL
└── app/
    ├── config.py                   # shared settings + per-portal credentials
    ├── db.py                       # shared engine/session/Base (unchanged)
    ├── core/                       # SHARED across scrapers
    │   ├── run_manager.py          # generic run tracking (moved from services/)
    │   ├── base_scraper.py         # shared Selenium: driver, download waiter, screenshots
    │   └── filenames.py            # sanitize_filename + timestamp helpers
    └── scrapers/
        ├── myflorida/
        │   ├── router.py           # /myflorida/*  endpoints
        │   ├── scraper.py          # MFMPScraper   (from services/mfmp_scraper.py)
        │   ├── models.py           # ScrapeRun, Bid
        │   ├── ingest.py           # Excel → DB    (from services/excel_ingest.py)
        │   └── commodity_codes.py  # (from data/commodity_codes.py)
        └── ridemetro/
            ├── router.py           # /ridemetro/* endpoints
            ├── scraper.py          # RideMetroScraper
            ├── models.py           # RideMetroRun, RideMetroBid
            └── export.py           # DB → Excel (openpyxl)
```

This gives true microservice-style separation: each portal is an isolated package
with its own router mounted under a URL prefix (`/myflorida/*`, `/ridemetro/*`),
its own scraper, models, and data. It can later be split into a separately
deployed service with almost no change, because portals share only `core/` and
`db.py`.

**Migration of the existing MFMP code (mechanical, behavior unchanged).** The
restructure relocates the current MFMP files and updates their imports; no MFMP
logic changes. Concretely:

| From (current) | To (new) |
|---|---|
| `app/services/mfmp_scraper.py` | `app/scrapers/myflorida/scraper.py` |
| `app/services/excel_ingest.py` | `app/scrapers/myflorida/ingest.py` |
| `app/data/commodity_codes.py` | `app/scrapers/myflorida/commodity_codes.py` |
| `app/routes/scraper.py` | `app/scrapers/myflorida/router.py` |
| `app/models.py` (ScrapeRun, Bid) | `app/scrapers/myflorida/models.py` |
| `app/services/run_manager.py` | `app/core/run_manager.py` (generalized) |
| driver setup / `wait_for_download` / screenshots (in mfmp_scraper) | `app/core/base_scraper.py` |
| `sanitize_filename` (in run_manager) | `app/core/filenames.py` |

MFMP endpoints move under the `/myflorida/*` prefix; `main.py` mounts both
routers; `create_tables.py` imports both scrapers' models. After the move, MFMP is
re-verified (imports clean, `/myflorida/categories` serves, tables still register)
so nothing regresses.

## 3. Environment files

Add RideMetro credentials to **server/.env** (and `.env.example`) alongside the
existing MFMP ones and shared `DATABASE_URL`:

```
# RideMetro (Bonfire)
RIDEMETRO_EMAIL=your_email
RIDEMETRO_PASSWORD=your_password
RIDEMETRO_LOGIN_URL=https://ridemetro.bonfirehub.com/login
RIDEMETRO_OPPORTUNITIES_URL=https://ridemetro.bonfirehub.com/portal/?tab=openOpportunities
```

The opportunities-list URL must be confirmed against the live portal (the URL you
gave, `/opportunities/174351`, is a single opportunity's detail page; the list of
"Open Public Opportunities" lives on the portal landing/opportunities tab).

## 4. Output folders

RideMetro run folders live under the shared documents root (same
`server/documents/` the MFMP scraper uses), self-identified by name:

```
server/documents/
└── Document_Bids_RideMetro (2026-07-08 14-30-05)/     # one per run
    ├── <Ref-1234> - <Project Title>.zip               # one zip per opportunity
    ├── <Ref-5678> - <Project Title>.zip
    └── RideMetro_Bids (2026-07-08 14-30-05).xlsx       # generated from the DB
```

- The run-folder name uses the run start date and time. The time uses hyphens
  (`14-30-05`) not colons, since `:` is unsafe in file names on some systems.
- Each zip is renamed from the browser's download to a sanitized
  `<Ref #> - <Project>` so bids are identifiable; unnamed/duplicate zips get an
  index suffix.

## 5. Database

New table for RideMetro (generated Excel is built from it). Following the MFMP
pattern (a runs table + a bids table):

**`ridemetro_runs`** — one row per run
- `run_id` (PK), `status`, `started_at`, `finished_at`
- `opportunities_found`, `documents_downloaded`, `excel_path`, `folder`

**`ridemetro_bids`** — one row per opportunity, columns mirror the Project Details
- `id` (PK), `run_id` (FK → ridemetro_runs)
- `project`, `ref_number`, `department`, `type`, `status`
- `open_date`, `intent_to_bid_due_date`, `question_due_date`, `close_date`
- `days_left`, `contact_information`, `project_description`
- `opportunity_url`, `zip_filename`, `scraped_at`
- `raw_data` (JSONB) — the full scraped field map, so nothing is lost if the
  portal shows extra/renamed fields

Unique constraint on `(run_id, ref_number)` to avoid duplicates within a run
(upsert on re-run), same approach as MFMP.

`create_tables.py` will import both scrapers' models so `Base.metadata.create_all`
creates MFMP and RideMetro tables together.

## 6. Scraper flow (`scrapers/ridemetro/scraper.py`)

Built on the shared `core/base_scraper.py` (Selenium Chrome, per-run download dir,
`wait_for_download`, `WebDriverWait`, failure screenshots) — the same machinery
the MFMP scraper uses.

1. **Login** — open `RIDEMETRO_LOGIN_URL`, fill email/password from `.env`, submit,
   wait for the portal to load.
2. **Open opportunities list** — go to `RIDEMETRO_OPPORTUNITIES_URL`; wait for the
   Open Public Opportunities table.
3. **Collect opportunities** — read each row; capture the row identity and its
   **View Opportunity** action link. Handle pagination if the table is paged.
4. **Per opportunity** (one by one):
   - Click **View Opportunity** → detail page opens (may be a new tab; handle
     window/tab switch and close-back).
   - Scrape the **Project Details** section into a field dict (label → value for
     Project, Ref. #, Department, Type, Status, Open Date, Intent to Bid Due Date,
     Question Due Date, Contact Information, Close Date, Days Left, Project
     Description).
   - Scroll to **Supporting Documentation**, click **Download All files**, wait for
     the zip to finish (no `.crdownload`; longer timeout since zips can be large).
   - Move/rename the zip into the run folder as `<Ref #> - <Project>.zip`.
   - Upsert the scraped details into `ridemetro_bids` (with `run_id`,
     `opportunity_url`, `zip_filename`).
   - Return to the list and continue.
5. **Generate Excel from DB** — after all opportunities, query `ridemetro_bids`
   for this run and write `RideMetro_Bids (<date> <time>).xlsx` (headers = the
   Project Details fields) into the run folder with openpyxl.
6. **Finish** — mark the run complete with counts (opportunities, zips, excel path).

### Resilience (same posture as MFMP)

- Explicit `WebDriverWait` on every navigation; no blind sleeps.
- Per-opportunity try/except: one failing opportunity logs an error and continues.
- Retries on flaky steps (login, zip download).
- Failure screenshots saved into the run folder.
- Filename sanitization for Ref #/Project; DB failures don't fail the scrape.
- `HEADLESS` flag honored (watch the browser while verifying selectors).

## 7. API endpoints (`scrapers/ridemetro/router.py`)

Mounted under `/ridemetro`, mirroring the MFMP endpoints:

| Endpoint | Method | Purpose |
|---|---|---|
| `/ridemetro/scrape` | POST | Start a run (background task); returns `run_id` |
| `/ridemetro/scrape/status/{run_id}` | GET | Live progress: step, opportunities found/processed, docs, errors |
| `/ridemetro/scrape/runs` | GET | List past runs |
| `/ridemetro/bids` | GET | Stored opportunities (filter by `run_id`) |

Existing MFMP endpoints move under `/myflorida/*`; the frontend
is updated accordingly. Runs execute as background tasks and the run state is
tracked by the shared `core/run_manager.py`, generalized to hold a `scraper` field
and per-scraper counts.

## 8. Dependencies

No new packages required — `selenium`, `webdriver-manager`, `openpyxl`,
`sqlalchemy`, `psycopg2-binary` are already installed. (Zip files need no special
handling; we store them as-is. `openpyxl` writes the generated Excel.)

## 9. Frontend (optional / secondary)

You emphasized the server side, so this is a follow-up: add a **portal switcher**
to the Next.js dashboard (MyFlorida | RideMetro). The RideMetro view has a simple
"Start scrape" button (no commodity categories), the same live status panel, and a
results table of scraped opportunities. API calls target `/ridemetro/*` via the
existing `NEXT_PUBLIC_API_URL`.

## 10. Implementation order

1. **Restructure** — move MFMP into `scrapers/myflorida/`, extract shared `core/`
   (run_manager, base_scraper, filenames); update imports, `main.py`,
   `create_tables.py`; verify MFMP still imports and serves `/myflorida/categories`
   and its tables still register (no regression).
2. **Config** — add RIDEMETRO_* env vars to config, `.env`, `.env.example`.
3. **Models** — `ridemetro_runs`, `ridemetro_bids`; wire into `create_tables.py`.
4. **Scraper** — login → opportunities list → per-opportunity Project Details +
   zip download (verify selectors headful against the live portal).
5. **DB → Excel** — `export.py` builds the run's xlsx from `ridemetro_bids`.
6. **API** — `/ridemetro/*` router; mount in `main.py`.
7. **Verify** — end-to-end run; confirm run folder has one zip per bid + the Excel,
   and rows land in `ridemetro_bids`.
8. **Frontend** (optional) — portal switcher + RideMetro view.

## 11. Open questions / assumptions

- **Selectors and URLs are assumptions** until verified against the live Bonfire
  portal — the opportunities-list URL, the **View Opportunity** button, the
  **Project Details** field layout, and the **Download All files** button. Verify
  headful (`HEADLESS=false`) and adjust.
- **Login** — assumed plain email/password with no SSO/2FA/CAPTCHA. Bonfire
  sometimes uses SSO; if so we need a manual-assist or token step.
- **Detail page navigation** — "View Opportunity" may open a new browser tab;
  the flow handles tab switching and returning to the list.
- **Field extraction** — Project Details labels are matched by their visible label
  text; the full scraped map is also stored in `raw_data` so extra fields aren't
  lost.
- **Run folder location** — placed under `server/documents/` (shared root),
  self-identified by the `Document_Bids_RideMetro (...)` name. Can instead nest
  under `documents/ridemetro/` if you prefer per-portal subfolders.
- **Excel source** — generated from the DB (not downloaded from the portal), per
  your instruction.
