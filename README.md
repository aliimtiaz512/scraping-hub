# Scraping Hub — Public Procurement Bid Scrapers

A multi-portal bid scraper. Each portal is a self-contained "microservice" module
under `server/app/scrapers/`, mounted at its own URL prefix and sharing common
Selenium/DB infrastructure in `server/app/core/`.

## Portals

- **MyFlorida** (MyFloridaMarketPlace) — scrapes advertisements by commodity-code
  category, downloads each bid's documents, and stores the portal's Excel export.
  Supports ad-status filtering. See `plan.md`.
- **RideMetro** (Bonfire) — scrapes Open Public Opportunities, downloads each
  opportunity's "Download All files" zip, stores the Project Details in the DB,
  and generates an Excel from the DB. Metadata/list-only by design (detail-page
  document downloads are intentionally dropped). See `plan_ride-metro.md`.
- **BidNet Direct** — logs in, searches a **curated keyword catalog** (grouped by
  sourcing track, one keyword per query), filters to "Member Agency Bids",
  paginates the results, opens each solicitation to scrape its fields and
  download every document into a per-bid folder, persists to the DB, and
  generates a per-run Excel. See `plan_bidnet-direct.md`.
- **Wisconsin** (eSupplier / PeopleSoft) — public bidder portal, **no login**.
  Searches Current Solicitations by keyword / agency / NIGP code (all optional),
  pages through the whole PeopleSoft results grid, stores every row, and
  generates an Excel from the DB.

## Structure

```
client/                         # Next.js dashboard (portal switcher)
server/
├── main.py                     # FastAPI app; mounts all four portal routers
├── create_tables.py            # creates all eight tables
└── app/
    ├── config.py               # settings + per-portal credentials
    ├── db.py                   # SQLAlchemy engine/session/Base
    ├── core/                   # SHARED: run_manager, base_scraper, filenames, models
    └── scrapers/
        ├── myflorida/          # scraper, models, ingest, commodity_codes, router
        ├── ridemetro/          # scraper, models, export (DB→Excel), router
        ├── bidnet/             # scraper, models, keywords, export, router
        └── wisconsin/          # scraper, models, export, router
```

## API

Each portal exposes:

- `POST /<portal>/scrape` — start a run (returns a `run_id`)
- `GET /<portal>/scrape/status/{run_id}` — poll run status
- `GET /<portal>/scrape/runs` — list past runs
- `GET /<portal>/bids` — list stored bids (with `query` / `run_id` / paging)

Portal-specific extras:

- MyFlorida: `GET /myflorida/categories`
- BidNet: `GET /bidnet/keywords` (curated catalog), `GET /bidnet/export` (Excel of all stored bids)

`GET /` is a health check listing the mounted scrapers.

## Setup

### Server

```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in creds and DATABASE_URL (see below)
.venv/bin/python create_tables.py
.venv/bin/uvicorn main:app --reload --port 8000
```

Credentials in `server/.env`:

- MyFlorida: `MFMP_EMAIL`, `MFMP_PASSWORD`
- RideMetro: `RIDEMETRO_EMAIL`, `RIDEMETRO_PASSWORD`
- BidNet Direct: `BIDNET_USERNAME`, `BIDNET_PASSWORD`
- Wisconsin: none (public portal; `WISCONSIN_URL` is optional)

### Database

Set `DATABASE_URL` in `server/.env`, e.g.:

```
DATABASE_URL=postgresql+psycopg2://myuser:mypass@localhost:5432/scraping-hub
```

`create_tables.py` (and server startup) create eight tables — a `*_runs` and a
`*_bids` table per portal:

- `scrape_runs`, `mfmp_bids` — MyFlorida
- `ridemetro_runs`, `ridemetro_bids` — RideMetro
- `bidnet_runs`, `bidnet_bids` — BidNet Direct
- `wisconsin_runs`, `wisconsin_bids` — Wisconsin

Each `*_bids` table maps the Excel columns to real columns and keeps the full row
in a `raw_data` JSONB column. MyFlorida parses its downloaded Excel into
`mfmp_bids`; the other portals scrape into their `*_bids` table and then
**generate** the run's Excel from the DB. In-flight runs are persisted so the
frontend sees a terminal status (instead of a 404) after a server restart.

### Client

```bash
cd client
npm install
# .env should contain: NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev
```

Open http://localhost:3000 and use the MyFlorida / RideMetro / BidNet Direct /
Wisconsin tabs.

## Notes

- Selenium uses Chrome; `webdriver-manager` downloads the matching driver
  automatically on first run.
- Set `HEADLESS=false` in `server/.env` to watch the browser while verifying or
  adjusting the portal selectors (the `SEL` dict at the top of each scraper:
  `server/app/scrapers/<portal>/scraper.py`).
- Downloads land under `DOWNLOAD_DIR` (default `data/documents/` at the repo root,
  kept outside `server/` so downloads don't trip the uvicorn `--reload` watcher):
  - MyFlorida: `run_<timestamp>/<bid title>/…` + `bids_export.xlsx`
  - RideMetro: `Document_Bids_RideMetro (<date> <time>)/` + `RideMetro_Bids (…).xlsx`
  - BidNet: `Document_Bids_BidnetDirect (<date> <time>)/` with a per-bid folder + run Excel
  - Wisconsin: `Document_Bids_Wisconsin (<date> <time>)/` + `Wisconsin_<date>_<time>.xlsx`
- CORS allows any `localhost` / `127.0.0.1` port so Next's dev server can
  auto-increment past `3000`.
- Logins are assumed to be plain email/password (no SSO/MFA/CAPTCHA).
