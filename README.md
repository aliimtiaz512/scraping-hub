# Scraping Hub — Public Procurement Bid Scrapers

A multi-portal bid scraper. Each portal is a self-contained module under
`server/app/scrapers/`, mounted at its own URL prefix and sharing common
Selenium / DB / run-tracking infrastructure in `server/app/core/`. A Next.js
console (`client/`) drives every portal, shows live run status, and downloads
results.

## Portals

**Document-downloading portals** (scrape metadata **and** download each bid's files):

- **MyFlorida** (MyFloridaMarketPlace) — searches advertisements by keyword or
  commodity-code category, filters by ad status/type, downloads each bid's
  documents, and downloads the portal's own Excel export which it ingests into
  the DB and merges into one workbook. See `plan.md`.
- **BidNet Direct** — pick one **niche** in the console; the run searches every
  keyword that niche owns **separately, one at a time** in a single session
  (never combined into a boolean query, which loses bids). Keywords live in the
  database (`bidnet_niches` / `bidnet_niche_keywords`, seeded from
  `app/scrapers/bidnet/niches.py`) and are never sent to the browser. Filters to
  "Member Agency Bids",
  applies the **sidebar filters** chosen in the frontend (status, NIGP category,
  organization, location, purchasing group, published/closing date, solicitation
  type, general requirements — see `docs/bidnet-sidebar-filters.md`), paginates
  the results, then opens each distinct solicitation once to scrape its fields
  and download every document. Everything lands in **one project folder** with a
  single master spreadsheet. See `plan_bidnet-direct.md`.
- **North Dakota** (ND Buys / Ivalua) — supplier login via ND OAuth (Azure AD
  B2C). The sign-in carries a reCAPTCHA, so **manual-login mode** opens a visible
  Chrome window and waits for a human to solve it; a persistent profile lets
  later runs skip the challenge. Downloads documents, persists, generates Excel.

**List-only portals** (metadata → DB → generated Excel, no document downloads):

- **RideMetro** (Bonfire / Euna Supplier Network) — logs in to the RideMetro
  portal, hops to **My Euna Supplier Network → My Network**, and sweeps every
  agency in the account's network whose registration Status is **Complete**
  (Incomplete ones have no portal behind their button), capturing each one's
  Open Public Opportunities. The export is a single agency-grouped Excel: a
  full-width banner per agency, its column headers, its rows, a blank row
  between blocks. List-only by design (detail-page document downloads are
  intentionally dropped). **Two accounts** — Hoope Lab and Fedpints — are
  selectable per run in the console; each is a separate login into a separate
  supplier network, so the choice decides which agencies get swept, and the
  account's name is in the report's filename.
- **Wisconsin** (eSupplier / PeopleSoft) — public bidder portal, **no login**.
  Searches Current Solicitations by keyword / agency / NIGP code (all optional)
  and pages through the whole PeopleSoft results grid.
- **SEPTA** — ASP.NET vendor portal; logs in and scrapes the Open Quotes grid
  (optional date / keyword / commodity-code filters).
- **SAM.gov** — searches active solicitations by updated-date range and NAICS
  code, extracts each notice with its attachments, and scores every bid through
  the evaluation funnel (see below). No credentials required.
- **Unison Marketplace** — vendored engine, extended to a full pipeline: reads
  the seller dashboard at **100 per page across every page**, opens each buy's
  detail page, stores its **General Buy Information**, line items and shipping
  (place of performance), **downloads every Buy Attachment**, and runs the bid
  through the **same evaluator SAM uses** (Company_Bid_Selection_Criteria.docx)
  for a PURSUE / REJECT / MANUAL_REVIEW verdict. The export carries the decision
  and its reason; the evaluator's working-out stays in the database.
  The one launch option is the portal's own **Filter By** criterion, picked in
  the console (default "Select Criteria" — the whole listing). The
  description-keyword exclusions and the shared 7-day close-date rule remain off
  for the testing phase; each is a boolean in
  `app/scrapers/unison/filters.py`.

**Reference / support:**

- **NAICS** — refreshes the public NAICS code reference (`naics_codes`), with a
  search endpoint. No credentials.
- **Cal eProcure** (BidSync BS3) — login verification and status panel.
- **Eval-config** — editable, DB-backed lists that tune the SAM evaluator:
  kill-words plus the Rule B (excluded) and Rule C (allowed) service lists.

## SAM.gov evaluation

Every SAM bid is scored **two-mode — `PURSUE` or `REJECT`** — by a deterministic,
requirement-type-first funnel (`server/app/scrapers/sam/engine/evaluator.py`),
per the company's accepted decision guide (`evaluation_criteria_sam_bids.docx`):

1. **Kill-word sieve** → instant REJECT (idiq / rfi / sources sought / market research).
2. **Requirement type** → HARDWARE / MATERIAL vs SERVICE (NAICS-primary, title confirms).
3. **Hardware** → PURSUE regardless of location.
4. **Excluded service (Rule B)** → REJECT regardless of location.
5. **Allowed service (Rule C)** → PURSUE only in the US Mainland, else REJECT.
6. **Any other service** → REJECT (not a validated in-scope requirement).

The Rule B / Rule C service lists and kill-words are seeded from config and
editable at runtime via the `/eval-config` endpoints. The generated SAM
workbook uses the same styling as the source portal (navy header, REJECT rows
tinted red, auto-fit columns).

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
  every portal) and `GET /runs/{id}/logs?after=N`.
- **Stopping**: a queued run is cancelled outright and never starts a browser; a
  running one is interrupted cooperatively as before. Both end as `stopped`.
- **Isolation**: each run gets its own workspace, Chrome profile and download
  directory. North Dakota's persistent profile is copied per run and saved back
  (Chrome locks a `--user-data-dir`), and SAM's attachment scratch space is
  per-run.

## Results delivery

Nothing accumulates in `data/documents`. A run works inside a temporary
workspace (`WORK_DIR`, the system temp dir by default); on completion it is
packaged into **one ZIP** — the cumulative Excel report plus every downloaded
document in its original niche-wise folders — stored in `ARCHIVE_DIR`
(`data/archives`), and the workspace is deleted. That single ZIP is then:

- **Downloadable** — `GET /runs/{run_id}/download` (buttons in the console's run
  status, run history, and Downloads tab).
- **Emailed** — on a successful run the ZIP is attached (or, if it exceeds the
  email size limit, just the cumulative Excel is attached and the ZIP link is in
  the body), via AWS SES, with an optional S3 upload. Wired into every scraping
  portal. Configured by `RECIPIENT_EMAILS` + the `AWS_*` / `PUBLIC_BASE_URL`
  settings; a blank `RECIPIENT_EMAILS` disables it.

### Excel presentation

Every portal's sheet looks the same, because they all format through
`app/core/excel_style.py`: a navy (`#1F4E78`) header row in bold white, centred,
thinly bordered, 26pt tall and frozen, with columns auto-sized to their longest
value (padded, capped at 60 so one long description can't push the rest off the
screen), and control characters Excel rejects stripped from every cell.

A plain export is then two calls — `new_workbook(title)` and
`write_table(sheet, headers, rows)`. Portals with more to say compose on top:
SAM tints REJECT/MANUAL_REVIEW rows, the MyFlorida sweep tints cross-listed
ones, and RideMetro repeats the standard header under each agency banner via
`style_header_row`. To restyle every report in the hub, change the constants at
the top of that one module.

## Structure

```
client/                         # Next.js console (portal switcher, run status, downloads)
server/
├── main.py                     # FastAPI app; mounts every portal router + the download router
├── create_tables.py            # creates all tables
└── app/
    ├── config.py               # settings, per-portal credentials, storage/delivery paths
    ├── db.py                   # SQLAlchemy engine/session/Base
    ├── core/                   # SHARED: run_manager, base_scraper, exports (ZIP), excel_style, download_router, filenames, models
    ├── services/               # notifier (SES email + S3)
    └── scrapers/
        ├── myflorida/  ridemetro/  bidnet/  wisconsin/  northdakota/
        ├── septa/      sam/         unison/  naics/      caleprocure/
        └── evalconfig/          # SAM kill-word / Rule B / Rule C lists
```

## API

Most portals expose:

- `POST /<portal>/scrape` — start a run (returns a `run_id`)
- `GET /<portal>/scrape/status/{run_id}` — poll run status
- `GET /<portal>/scrape/runs` — list past runs
- `GET /<portal>/bids` — list stored bids (with `query` / `run_id` / paging)

Cross-cutting and portal-specific extras:

- **Downloads:** `GET /runs/{run_id}/download` — the run's archive ZIP
- **MyFlorida:** `GET /myflorida/categories`
- **BidNet:** `GET /bidnet/niches` (niche dropdown), `GET /bidnet/filters` (sidebar filter catalog), `POST /bidnet/filters/refresh` (re-harvest the full option lists), `GET /bidnet/export` (Excel of all stored bids)
- **SAM:** `POST /sam/evaluate` (score a bid), `POST /sam/scrape/stop/{run_id}`, `GET /sam/screenshot/{run_id}`
- **NAICS:** `GET /naics` (list), `GET /naics/search`
- **Eval-config:** `GET /eval-config`, and `POST` / `DELETE` on `/eval-config/kill-words`, `/eval-config/excluded-services`, `/eval-config/allowed-services`

`GET /` is a health check listing the mounted scrapers.

## Setup

### Server

```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in creds, DATABASE_URL, and (optionally) notifications
.venv/bin/python create_tables.py
.venv/bin/uvicorn main:app --reload --port 8000
```

Credentials in `server/.env`:

- MyFlorida: `MFMP_EMAIL`, `MFMP_PASSWORD`
- RideMetro: `HOOPE_LAB_USERNAME`/`HOOPE_LAB_PASSWORD` and
  `FEDPINTS_USERNAME`/`FEDPINTS_PASSWORD` — one account per run, picked in the
  console. An account missing either key is shown as unavailable and cannot be
  started (`GET /ridemetro/accounts` reports which are configured). The former
  `RIDEMETRO_EMAIL`/`RIDEMETRO_PASSWORD` still work as the Hoope Lab account's
  credentials when the `HOOPE_LAB_*` pair is unset.
- BidNet Direct: `BIDNET_USERNAME`, `BIDNET_PASSWORD`
- North Dakota: `NORTHDAKOTA_USERNAME`, `NORTHDAKOTA_PASSWORD` (+ `NORTHDAKOTA_MANUAL_LOGIN`)
- SEPTA: `SEPTA_USERNAME`, `SEPTA_PASSWORD`
- Cal eProcure: `Cal_ePROCURE_USERNAME`, `Cal_ePROCURE_PASSWORD`
- Unison: `UNISON_EMAIL`, `UNISON_PASSWORD`
- Wisconsin / SAM / NAICS: none (public)

**Wrap any password containing punctuation in single quotes** —
`UNISON_PASSWORD='pa$$w0rd%#here'`. Unquoted, a `#` after a space starts a
comment and the rest of the password is silently dropped; double quotes stop
that but read `\b`-style escapes, so they corrupt a password with a backslash.
`.env.example` has the full matrix. A Unison run verifies this before it logs
in (`app/core/credentials.py`): it re-reads the raw `.env` line and compares it
against what the app loaded, then refuses to attempt the login if they differ —
so a mis-parsed password is reported as a mis-parsed password rather than as a
portal outage, and doesn't spend failed login attempts on a live vendor account.
The check logs only a length/character-class/SHA-8 fingerprint, never a value.

Storage & delivery (all optional — sensible defaults):

- `SCRAPE_CONCURRENCY` — how many scrapes may run at once; the rest queue (default 3, set by Chrome's memory footprint)
- `WORK_DIR` — scratch workspace for in-flight runs (default: system temp dir)
- `ARCHIVE_DIR` — where finished-run ZIPs are stored (default `../data/archives`)
- `PUBLIC_BASE_URL` — base URL for the download link in emails (default `http://localhost:8000`)
- Notifications: `RECIPIENT_EMAILS`, `AWS_S3_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`,
  `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `AWS_SES_FROM_EMAIL`,
  `AWS_SES_USERNAME`, `AWS_SES_PASSWORD`

### Database

Set `DATABASE_URL` in `server/.env`, e.g.:

```
DATABASE_URL=postgresql+psycopg2://myuser:mypass@localhost:5432/scraping-hub
```

`create_tables.py` (and server startup) create a `run_state` table (shared
run-persistence, so an in-flight run survives a restart) plus a `*_runs` / `*_bids`
pair per portal and a few singletons:

- `scrape_runs`, `mfmp_bids` — MyFlorida
- `ridemetro_runs`, `ridemetro_bids` — RideMetro
- `bidnet_runs`, `bidnet_bids`, `bidnet_niches`, `bidnet_niche_keywords` — BidNet Direct
- `wisconsin_runs`, `wisconsin_bids` — Wisconsin
- `northdakota_runs`, `northdakota_bids` — North Dakota
- `septa_runs`, `septa_bids` — SEPTA
- `sam_runs`, `sam_bids` — SAM.gov
- `unison_runs`, `unison_requests` — Unison
- `naics_codes` — NAICS reference
- `eval_config` — SAM kill-word / Rule B / Rule C lists

Each `*_bids` table maps the Excel columns to real columns and keeps the full row
in a `raw_data` JSONB column. MyFlorida parses its downloaded Excel into
`mfmp_bids`; the other portals scrape into their `*_bids` table and then
**generate** the run's Excel from the DB when it is packaged or downloaded.

### Client

```bash
cd client
npm install
# .env should contain: NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev
```

Open http://localhost:3000 and pick a portal from the console.

## Notes

- Selenium uses Chrome; `webdriver-manager` downloads the matching driver
  automatically on first run.
- Set `HEADLESS=false` in `server/.env` to watch the browser while verifying or
  adjusting portal selectors (the `SEL` dict at the top of each
  `server/app/scrapers/<portal>/scraper.py`). North Dakota's manual-login mode
  always forces a visible window regardless of this setting.
- Results live only as archive ZIPs under `ARCHIVE_DIR` — nothing is written to
  `data/documents` for new runs. Runs made before this change are still
  downloadable from their old location.
- CORS allows any `localhost` / `127.0.0.1` port so Next's dev server can
  auto-increment past `3000`.
- Logins are assumed to be plain email/password. The exception is North Dakota,
  whose B2C sign-in carries a reCAPTCHA handled by manual-login mode.
```
