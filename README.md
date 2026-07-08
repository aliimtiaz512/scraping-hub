# Scraping Hub — Public Procurement Bid Scrapers

A multi-portal bid scraper. Each portal is a self-contained "microservice" module
under `server/app/scrapers/`, mounted at its own URL prefix and sharing common
Selenium/DB infrastructure in `server/app/core/`.

Portals:

- **MyFlorida** (MyFloridaMarketPlace) — scrapes advertisements by commodity-code
  category, downloads each bid's documents, and stores the portal's Excel export.
  See `plan.md`.
- **RideMetro** (Bonfire) — scrapes Open Public Opportunities, downloads each
  opportunity's "Download All files" zip, stores the Project Details in the DB,
  and generates an Excel from the DB. See `plan_ride-metro.md`.

## Structure

```
client/                         # Next.js dashboard (portal switcher)
server/
├── main.py                     # FastAPI app; mounts both portal routers
├── create_tables.py            # creates all four tables
└── app/
    ├── config.py               # settings + per-portal credentials
    ├── db.py                   # SQLAlchemy engine/session/Base
    ├── core/                   # SHARED: run_manager, base_scraper, filenames
    └── scrapers/
        ├── myflorida/          # scraper, models, ingest, commodity_codes, router
        └── ridemetro/          # scraper, models, export (DB→Excel), router
```

Each portal exposes: `POST /<portal>/scrape`, `GET /<portal>/scrape/status/{run_id}`,
`GET /<portal>/scrape/runs`, `GET /<portal>/bids`. MyFlorida also has
`GET /myflorida/categories`.

## Setup

### Server

```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in MFMP_* and RIDEMETRO_* creds and DATABASE_URL
.venv/bin/python create_tables.py
.venv/bin/uvicorn main:app --reload --port 8000
```

### Database

Set `DATABASE_URL` in `server/.env`, e.g.:

```
DATABASE_URL=postgresql+psycopg2://myuser:mypass@localhost:5432/scraping-hub
```

`create_tables.py` (and server startup) create:

- `scrape_runs`, `mfmp_bids` — MyFlorida runs and bids (Excel columns mapped, full
  row kept in a `raw_data` JSONB column)
- `ridemetro_runs`, `ridemetro_bids` — RideMetro runs and opportunities (Project
  Details fields, plus `raw_data` JSONB)

MyFlorida parses its downloaded Excel into `mfmp_bids`. RideMetro scrapes Project
Details into `ridemetro_bids`, then **generates** the run's Excel from the DB.

### Client

```bash
cd client
npm install
# .env should contain: NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev
```

Open http://localhost:3000 and use the MyFlorida / RideMetro tabs.

## Notes

- Selenium uses Chrome; `webdriver-manager` downloads the matching driver
  automatically on first run.
- Set `HEADLESS=false` in `server/.env` to watch the browser while verifying or
  adjusting the portal selectors (the `SEL` dict at the top of each scraper:
  `server/app/scrapers/<portal>/scraper.py`).
- Output folders under `server/documents/`:
  - MyFlorida: `run_<timestamp>/<bid title>/…` + `bids_export.xlsx`
  - RideMetro: `Document_Bids_RideMetro (<date> <time>)/` with one zip per
    opportunity + `RideMetro_Bids (<date> <time>).xlsx`
- Portal selectors are placeholders until verified against the live sites, and
  logins are assumed to be plain email/password (no SSO/MFA/CAPTCHA).
