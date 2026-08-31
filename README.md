# Scraping Hub — Public Procurement Bid Scrapers

A multi-portal bid scraper. Each portal is a self-contained module under
`server/app/scrapers/`, mounted at its own URL prefix and sharing common
Selenium / DB / run-tracking infrastructure in `server/app/core/`. A Next.js
console (`client/`) drives every portal, shows live run status, and downloads
results.

Twelve portals plus a reference tool are mounted today. Whatever a portal
scrapes, every run ends the same way: rows in its own `*_bids` table, a styled
Excel generated from that table, and one downloadable archive.

## Portals

### Document-downloading portals

Scrape metadata **and** keep each bid's files; the download is a ZIP.

- **MyFlorida** (MyFloridaMarketPlace) — searches advertisements by keyword or
  commodity-code category, filters by ad status/type, downloads each bid's
  documents, and downloads the portal's own Excel export which it ingests into
  the DB and merges into one workbook. See `plan.md`.
- **MyFlorida ad-status sweep** — a second, separately mounted flow over the
  same portal (`/myflorida/sweep`): captures every ad in a status window, keeps
  its attachments, and reports a summary sheet beside the documents it indexes.
  Niches live in `app/scrapers/myflorida/sweep/mfmp_niches.yaml`. See
  `docs/myflorida-capture-pipeline.md`.
- **North Dakota** (ND Buys / Ivalua) — supplier login via ND OAuth (Azure AD
  B2C). The sign-in carries a reCAPTCHA, so **manual-login mode** opens a visible
  Chrome window and waits for a human to solve it; a persistent profile lets
  later runs skip the challenge. Downloads documents, persists, generates Excel.
- **BidNet Direct** — every run applies the **sidebar filters** chosen in the
  console (status, NIGP category, organization, location, purchasing group,
  published/closing date, solicitation type, general requirements — see
  `docs/bidnet-sidebar-filters.md`), scrapes the "Member Agency Bids" group,
  paginates, and opens each distinct solicitation once. Three ways to launch:
  - **One niche** — searches every keyword and NIGP code that niche owns
    **separately, one at a time** in a single session (never combined into a
    boolean query, which loses bids). Terms live in the database
    (`bidnet_niches` / `bidnet_niche_keywords`, seeded from
    `app/scrapers/bidnet/niches.py`). Writes `<Niche>_Bids.xlsx` into the day's
    shared session root, whose whole ZIP is the download.
  - **Run all niches** — every niche in the catalog, one after another, each in
    its own browser session and folder. Delivers one
    `BidNet_Niche_Bids_<date>.zip` holding one spreadsheet per niche.
  - **Run all member agency bids** — no niche and no keywords: the sidebar
    filters alone, applied to the whole Member Agency Bids list. Delivers a
    single consolidated `bidnet_member_agencie_<date>.xlsx` with no ZIP around
    it; its `Niche` column names the issuing agency.

  See `plan_bidnet-direct.md` and `docs/bidnet-*.md`.
- **City of Philadelphia** (PHLContracts, Periscope BSO) — vendor login through
  a PrimeFaces overlay, then the Bids tab → Open Bids → View More. Stores the
  header fields and line items of each bid, downloads its attachments, and runs
  it through the **shared evaluation matrix** (below). Line items are what let
  the matrix tell a supply from a service on a one-line description. See
  `docs/philadelphia-phlcontracts.md`.

### List-only portals

Metadata → DB → generated Excel. The download is a bare `.xlsx`; no ZIP, because
a ZIP around a single sheet is a folder to unpack for nothing.

- **EMMA** (eMaryland Marketplace Advantage / Ivalua) — signs in, opens
  **Sourcing → Public Solicitations**, applies the portal's own **Keywords /
  Status / Category** filters, and pages the whole results grid. Every bid is
  then screened against a **keyword blocklist** (below) and only the passing
  ones are reported. Documents are downloaded solely so their text can be
  screened, then deleted.
- **SEPTA** — ASP.NET vendor portal. One run searches **exactly one module**:
  *Open Quotes* (a parts-requisition feed) or *Open Bids* (actual
  solicitations), with an optional open-ended "opens from" date. The two grids
  describe different things and stay apart end to end, which is why the module
  is a run parameter rather than both being scraped. Rows naming a blacklisted
  term (`app/scrapers/septa/exclusions.py` — bus/engine manufacturers and
  gaskets) are skipped outright.
- **RideMetro** (Bonfire / Euna Supplier Network) — logs in, hops to **My Euna
  Supplier Network → My Network**, and sweeps every agency whose registration
  status is **Complete** (Incomplete ones have no portal behind their button),
  capturing each one's Open Public Opportunities. The export is a single
  agency-grouped Excel: a full-width banner per agency, its headers, its rows, a
  blank row between blocks. **Two accounts** — Hoope Lab and Fedpints — are
  selectable per run; each is a separate login into a separate supplier network.
- **Wisconsin** (eSupplier / PeopleSoft) — public bidder portal, **no login**.
  Searches Current Solicitations by keyword / agency / NIGP code (all optional)
  and pages through the whole PeopleSoft results grid.
- **SAM.gov** — searches active solicitations by updated-date range and NAICS
  code, extracts each notice with its attachments, and scores every bid through
  the evaluation matrix. Attachments are read for their text, then deleted. No
  credentials required.
- **Unison Marketplace** — reads the seller dashboard at **100 per page across
  every page**, opens each buy's detail page, stores its General Buy
  Information, line items and shipping (place of performance), reads every Buy
  Attachment, and runs the bid through the evaluation matrix. The one launch
  option is the portal's own **Filter By** criterion. See
  `docs/unison-pagination-and-screens.md`.

### Reference / support

- **NAICS** — refreshes the public NAICS code reference (`naics_codes`), with a
  search endpoint and a file import. No credentials.
- **Cal eProcure** (BidSync BS3) — login verification and status panel.
- **Eval-config** — editable, DB-backed lists that tune the evaluation matrix:
  kill-words plus the Rule B (excluded) and Rule C (allowed) service lists.

## Screening and evaluation

Two different mechanisms decide what a run reports. They are independent, and a
portal uses whichever suits the data it has.

### The evaluation matrix — SAM, Unison, Philadelphia

One engine (`app/scrapers/sam/engine/evaluator.py`), implementing the company's
accepted decision guide. Unison and Philadelphia reach it through
`app.scrapers.sam.evaluation.evaluate` rather than reimplementing it, so a
PURSUE on one sheet means the same thing as a PURSUE on another. Three verdicts
— **PURSUE**, **REJECT**, **MANUAL_REVIEW**:

1. **Kill-word sieve** → instant REJECT (rfi / sources sought / market research).
2. **Requirement type** → HARDWARE/MATERIAL vs SERVICE (NAICS-primary, title
   confirms; a portal with structural proof of a supply — Unison's and
   Philadelphia's line-item tables — may promote a bid to HARDWARE).
3. **Hardware** → PURSUE regardless of location.
4. **Excluded service (Rule B)** → REJECT regardless of location.
5. **Allowed service (Rule C)** → PURSUE in the US Mainland, else REJECT.
6. **Service on neither list** → MANUAL_REVIEW in the US Mainland, REJECT
   outside it.

The Rule B / Rule C lists and kill-words are seeded from config and editable at
runtime via `/eval-config`. A **local Ollama model** sits behind the rule engine
as an optional wall (`app/scrapers/sam/ollama_bridge.py`): it is consulted only
for bids the rules left at MANUAL_REVIEW, receives a ~400-token structured brief
and never raw bid text, and any failure leaves the verdict untouched. It runs in
shadow mode by default (`OLLAMA_SHADOW_MODE`), recording its opinion to audit
columns without changing the stored decision.

### The keyword blocklist — EMMA

EMMA exposes no description, no NAICS-equivalent and no place-of-performance
field, so the matrix has nothing to reason over. It uses a plain blocklist
instead (`app/scrapers/emma/evaluation.py`): if any blocked phrase appears in
the bid's own fields **or in any of its documents**, the bid is rejected;
everything else passes, and only passing bids are exported.

- Two editable lists at the top of that module — the out-of-scope keywords, and
  master-contract phrases (solicitations open only to existing master-contract
  holders). Edit the lists; nothing else needs touching.
- Matching is whole-phrase and word-boundary anchored, so "Construction" never
  fires on "constructive" and "Audit" never on "auditorium". Whitespace is
  flexible (a phrase split across lines in extracted PDF text still counts) and
  the final word matches singular or plural.
- Screening runs in two stages: first on the grid fields the search already
  returned — a bid rejected there never has its detail page opened — then on the
  text of its documents. On live data the first stage alone skips ~40% of bids.

### The close-date filter — every scraping portal

`app/core/closing_filter.py` keeps only bids closing at least
`MIN_DAYS_UNTIL_CLOSE` (7) days out, so there is runway to prepare a submission.
A bid whose close date cannot be read is **kept** and counted — a drop is never
silent. Each run reports how many it skipped and how many it kept unverified.

## Running jobs

Every scrape runs on a dedicated thread pool (`app/core/jobs.py`), not on the
request that started it — so a run belongs to the server, and navigating the
console, closing the tab or switching portals has no effect on it.

- **Concurrency is capped** at `SCRAPE_CONCURRENCY` (default 3). Each run drives
  its own Chrome at roughly 300–500 MB; past the cap, runs wait as `queued`
  rather than all starting and one being OOM-killed mid-flight.
- **The pool is separate from the API's.** FastAPI dispatches sync endpoints
  into anyio's shared 40-thread pool; a scrape parked there for ten minutes
  holds a thread the API needs. Scrapes never touch it.
- **Active Jobs** — a bar in the console footer, on every page, listing every
  in-flight run across all portals with its step, elapsed time, live log tail
  and a Stop control. Backed by `GET /runs?active=true` (one request covers
  every portal) and `GET /runs/{id}/logs?after=N`. Log tails are collected by
  thread attribution (`app/core/run_logs.py`), so no scraper knows about them.
- **Live preview** — a modal streaming frames from the run's browser
  (`app/core/live.py`, `GET /runs/{id}/screenshot`), available while a run is
  in flight on any portal.
- **Stopping**: a queued run is cancelled outright and never starts a browser; a
  running one is interrupted cooperatively. Both end as `stopped`.
- **Isolation**: each run gets its own workspace, Chrome profile and download
  directory. North Dakota's persistent profile is copied per run and saved back
  (Chrome locks a `--user-data-dir`), and attachment scratch space is per-run.
- **Resilience**: a browser that dies mid-run (OOM, crash, a closed live-preview
  window) is detected rather than spinning; long document crawls restart and
  re-login once, and recycle the browser periodically. A stale
  `webdriver-manager` lock — which otherwise fails every run before Chrome even
  starts — is cleared automatically.

## Results delivery

Nothing accumulates in `data/documents`. A run works inside a temporary
workspace (`WORK_DIR`, the system temp dir by default); on completion it is
packaged into `ARCHIVE_DIR` (`data/archives`) and the workspace is deleted.

What gets packaged depends on the run, and `exports.is_excel_only` is the single
question both the packaging step and the download endpoint ask — so the two
cannot disagree about what a run produced:

- **A ZIP** — the cumulative Excel plus every downloaded document in its
  original folders, for the document-downloading portals.
- **A bare `.xlsx`** — for portals whose only output is the spreadsheet
  (`EXCEL_ONLY_PORTALS`: SAM, SEPTA, RideMetro, Unison, EMMA), and for
  individual runs that say so themselves (BidNet's member-agency sweep).

Either way the artifact is:

- **Downloadable** — `GET /runs/{run_id}/download` (buttons in the console's run
  status, run history, and Downloads tab).
- **Emailed** — on a successful run the archive is attached (or, if it exceeds
  the email size limit, just the Excel is attached and the link is in the body),
  via AWS SES, with an optional S3 upload. Configured by `RECIPIENT_EMAILS` +
  the `AWS_*` / `PUBLIC_BASE_URL` settings; a blank `RECIPIENT_EMAILS` disables
  it.

### Excel presentation

Every portal's sheet looks the same, because they all format through
`app/core/excel_style.py`: a navy (`#1F4E78`) header row in bold white, centred,
thinly bordered, 26pt tall and frozen, with columns auto-sized to their longest
value (padded, capped at 60 so one long description can't push the rest off the
screen), and control characters Excel rejects stripped from every cell.

A plain export is two calls — `new_workbook(title)` and
`write_table(sheet, headers, rows)`. Portals with more to say compose on top:
SAM tints REJECT/MANUAL_REVIEW rows, the MyFlorida sweep tints cross-listed
ones, and RideMetro repeats the standard header under each agency banner via
`style_header_row`. To restyle every report in the hub, change the constants at
the top of that one module.

## Structure

```
client/                    # Next.js console
├── src/app/               # /, /console, /console/[portal]{,/history,/exports}
├── src/components/        # per-portal panels + results tables, ActiveJobs,
│                          #   LiveMonitor, StopButton, RunStatus, ui primitives
└── src/lib/               # api.ts (typed client), portals.ts, runs.ts
docs/                      # per-portal deep dives (BidNet, MyFlorida, Unison, Philadelphia)
server/
├── main.py                # FastAPI app; mounts every portal router + the shared run router
├── create_tables.py       # creates all tables
├── migrations/            # dated .sql migrations for existing databases
├── tests/                 # pytest suite (BidNet, jobs, credentials, excel style, …)
└── app/
    ├── config.py          # settings, per-portal credentials, storage/delivery paths
    ├── db.py              # SQLAlchemy engine/session/Base
    ├── core/              # SHARED
    │   ├── jobs.py          # the scrape pool: one cap, one queue
    │   ├── run_manager.py   # run state, persisted so a run survives a restart
    │   ├── run_logs.py      # per-run log tails by thread attribution
    │   ├── base_scraper.py  # Chrome lifecycle, navigation retries, stop support
    │   ├── live.py          # live-frame registry for the preview modal
    │   ├── exports.py       # ZIP / bare-xlsx packaging + archiving
    │   ├── excel_style.py   # the one look every sheet has
    │   ├── closing_filter.py# the shared 7-day close-date rule
    │   ├── credentials.py   # verifies a password survived .env parsing
    │   └── download_router.py # /runs endpoints (list, logs, screenshot, stop, download)
    ├── services/          # notifier (SES email + S3)
    └── scrapers/
        ├── myflorida/ (+ sweep/)  ridemetro/  bidnet/  wisconsin/  northdakota/
        ├── septa/  sam/  unison/  emma/  philadelphia/  caleprocure/
        ├── naics/                  # reference catalogue
        └── evalconfig/             # kill-word / Rule B / Rule C lists
```

## API

Most portals expose:

- `POST /<portal>/scrape` — start a run (returns a `run_id`)
- `GET /<portal>/scrape/status/{run_id}` — poll run status
- `GET /<portal>/scrape/runs` — list past runs
- `GET /<portal>/bids` — list stored bids (with `query` / `run_id` / paging)

Cross-cutting (any portal, one endpoint):

- `GET /runs?active=true` — every in-flight run across all portals
- `GET /runs/{id}/logs?after=N` — that run's recent log lines
- `GET /runs/{id}/screenshot` — a live browser frame, or null
- `POST /runs/{id}/stop` — cancel if queued, interrupt if running
- `GET /runs/{id}/download` — the run's archive (ZIP or bare `.xlsx`)

Portal-specific extras:

- **MyFlorida:** `GET /myflorida/categories`; the sweep adds
  `/myflorida/sweep/{niches,niches/reload,accounts,bids,scrape,…}`
- **BidNet:** `GET /bidnet/niches`, `GET /bidnet/filters`,
  `POST /bidnet/filters/refresh`, `POST /bidnet/scrape/batch` (all niches),
  `POST /bidnet/scrape/member-agencies`, `GET /bidnet/export`
- **RideMetro:** `GET /ridemetro/accounts` (which accounts are configured)
- **SEPTA:** `GET /septa/open-bids` (the Bid module's stored rows)
- **Philadelphia:** `GET /philadelphia/search/fields`
- **SAM:** `POST /sam/evaluate`, `POST /sam/scrape/stop/{run_id}`,
  `GET /sam/screenshot/{run_id}`
- **NAICS:** `GET /naics`, `GET /naics/search`, file import
- **Eval-config:** `GET /eval-config`, and `POST` / `DELETE` on
  `/eval-config/kill-words`, `/eval-config/excluded-services`,
  `/eval-config/allowed-services`

`GET /` is a health check listing the mounted scrapers.

## Setup

### Server

One-time setup:

```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in creds, DATABASE_URL, and (optionally) notifications
.venv/bin/python create_tables.py
```

Run the API:

```bash
cd server
.venv/bin/uvicorn main:app --reload --port 9000
```

`--port 9000` is not optional. Without it uvicorn takes its own default of 8000,
and that failure is quiet in both directions: every panel in the console reports
"Failed to fetch" (the client is built against `NEXT_PUBLIC_API_URL=http://localhost:9000`),
and every download link in a completion email points at `PUBLIC_BASE_URL`, which
nothing is then serving. If the console cannot reach the API, check the port
first: `curl -s localhost:9000/`.

Credentials in `server/.env`:

| Portal | Keys |
|---|---|
| MyFlorida (+ sweep) | `MFMP_EMAIL`, `MFMP_PASSWORD` |
| RideMetro | `HOOPE_LAB_USERNAME`/`HOOPE_LAB_PASSWORD`, `FEDPINTS_USERNAME`/`FEDPINTS_PASSWORD` |
| BidNet Direct | `BIDNET_USERNAME`, `BIDNET_PASSWORD` |
| North Dakota | `NORTHDAKOTA_USERNAME`, `NORTHDAKOTA_PASSWORD` (+ `NORTHDAKOTA_MANUAL_LOGIN`) |
| SEPTA | `SEPTA_USERNAME`, `SEPTA_PASSWORD` |
| Philadelphia | `PHILA_EMAIL`, `PHILA_PASSWORD` |
| EMMA | `EMMA_USERNAME`, `EMMA_PASSWORD` |
| Cal eProcure | `Cal_ePROCURE_USERNAME`, `Cal_ePROCURE_PASSWORD` |
| Unison | `UNISON_EMAIL`, `UNISON_PASSWORD` |
| Wisconsin / SAM / NAICS | none (public) |

RideMetro takes one account per run, picked in the console; an account missing
either key is shown as unavailable (`GET /ridemetro/accounts` reports which are
configured). The former `RIDEMETRO_EMAIL`/`RIDEMETRO_PASSWORD` still work as the
Hoope Lab account's credentials when the `HOOPE_LAB_*` pair is unset.

**Wrap any password containing punctuation in single quotes** —
`UNISON_PASSWORD='pa$$w0rd%#here'`. Unquoted, a `#` after a space starts a
comment and the rest of the password is silently dropped; double quotes stop
that but read `\b`-style escapes, so they corrupt a password with a backslash.
`.env.example` has the full matrix. A Unison run verifies this before it logs in
(`app/core/credentials.py`): it re-reads the raw `.env` line and compares it
against what the app loaded, then refuses to attempt the login if they differ —
so a mis-parsed password is reported as a mis-parsed password rather than as a
portal outage, and doesn't spend failed login attempts on a live vendor account.
The check logs only a length/character-class/SHA-8 fingerprint, never a value.

Storage & delivery (all optional — sensible defaults):

- `SCRAPE_CONCURRENCY` — how many scrapes may run at once; the rest queue (default 3)
- `WORK_DIR` — scratch workspace for in-flight runs (default: system temp dir)
- `ARCHIVE_DIR` — where finished-run archives are stored (default `../data/archives`)
- `PUBLIC_BASE_URL` — base URL for the download link in emails
- `OLLAMA_SHADOW_MODE` — keep the Ollama wall advisory-only (default true)
- Notifications: `RECIPIENT_EMAILS`, `AWS_S3_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`,
  `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `AWS_SES_FROM_EMAIL`,
  `AWS_SES_USERNAME`, `AWS_SES_PASSWORD`

### Database

Set `DATABASE_URL` in `server/.env`, e.g.:

```
DATABASE_URL=postgresql+psycopg2://myuser:mypass@localhost:5432/scraping-hub
```

`create_tables.py` (and server startup) create a `run_state` table (shared
run-persistence, so an in-flight run survives a restart) plus a `*_runs` /
`*_bids` pair per portal and a few singletons:

| Portal | Tables |
|---|---|
| MyFlorida | `scrape_runs`, `mfmp_bids` |
| MyFlorida sweep | `mfmp_sweep_bids`, `mfmp_sweep_scores` |
| RideMetro | `ridemetro_runs`, `ridemetro_bids` |
| BidNet Direct | `bidnet_runs`, `bidnet_bids`, `bidnet_niches`, `bidnet_niche_keywords` |
| Wisconsin | `wisconsin_runs`, `wisconsin_bids` |
| North Dakota | `northdakota_runs`, `northdakota_bids` |
| SEPTA | `septa_runs`, `septa_bids`, `septa_open_bids` |
| SAM.gov | `sam_runs`, `sam_bids` |
| Unison | `unison_runs`, `unison_requests` |
| EMMA | `emma_runs`, `emma_bids` |
| Philadelphia | `philadelphia_runs`, `city_of_philadelphia_bids` |
| Reference | `naics_codes`, `eval_config` |

Each `*_bids` table maps the Excel columns to real columns and keeps the full row
in a `raw_data` JSONB column. MyFlorida parses its downloaded Excel into
`mfmp_bids`; the other portals scrape into their `*_bids` table and then
**generate** the run's Excel from the DB when it is packaged or downloaded.

`server/migrations/` holds dated `.sql` files for evolving an existing database —
`create_tables.py` only creates what is missing, so a schema change to an
existing table is applied from there.

### Client

One-time setup:

```bash
cd client
npm install
cp .env.example .env   # then set NEXT_PUBLIC_API_URL=http://localhost:9000
```

Run the console:

```bash
cd client
npm run dev
```

Open http://localhost:4000 and pick a portal from the console. The API must
already be running on 9000, or every panel loads empty.

## Notes

- Selenium uses Chrome; `webdriver-manager` downloads the matching driver
  automatically on first run. A stale lock left by a killed process is cleared
  automatically rather than failing every later run.
- Browser visibility is decided per run, not globally: a run started from the
  console's **Live preview** button shows its window, every other run is
  headless. North Dakota's manual-login mode always forces a visible window.
- Selectors live in the `SEL` dict at the top of each
  `server/app/scrapers/<portal>/scraper.py`.
- Results live only as archives under `ARCHIVE_DIR` — nothing is written to
  `data/documents` for new runs. Runs made before this change are still
  downloadable from their old location.
- The API listens on `9000` and the console on `4000` (`npm run dev` passes
  `-p 4000`; the API port is the `--port` flag above). CORS allows any
  `localhost` / `127.0.0.1` port, so Next auto-incrementing past `4000` when it
  is taken still reaches the API.
- Logins are assumed to be plain email/password. The exception is North Dakota,
  whose B2C sign-in carries a reCAPTCHA handled by manual-login mode.
