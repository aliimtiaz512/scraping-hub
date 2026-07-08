# MyFloridaMarketPlace (MFMP) Bid Scraper — Plan

## 1. What I understand from the requirements

Build a web scraper for the MFMP vendor portal (`https://vendor.myfloridamarketplace.com/login`) that:

1. **Logs in** using credentials stored in the server's `.env` file.
2. **Navigates** to the **Advertisements** option in the nav bar after login.
3. **Scrolls down** to the search bar and opens **Advanced Search** (located under the search bar).
4. On the Advanced Search page, uses the **left-side dropdowns** and opens the **Commodity Codes** dropdown.
5. **Enters commodity codes based on a category the user picks in the frontend.** The categories and their codes come from the curated MFMP commodity-code document (105 codes across 6 categories). Example: if the user picks **UI/UX**, the scraper enters the UI/UX codes (81111820, 81112103, 43232404, ...).
6. Clicks the **Search** button at the end of the dropdowns.
7. Results appear in a **table of bids**. For **each bid row**, the scraper clicks the value in the **Number column** to open the bid's detail (interior) page.
8. On the bid detail page, it **scrolls down to the documents section** and **downloads every document**.
9. Documents are organized on disk:
   - A **main documents folder** at the root.
   - Inside it, **one folder per run** (timestamped).
   - Inside each run folder, **one folder per bid**.
   - Each downloaded document is **renamed using the bid title**.
10. After all bids are processed, the scraper clicks the **Export Excel** button on the MFMP results page and stores the downloaded Excel file (all bids info) **inside that run's folder** (`documents/<run>/bids_export.xlsx`) — not in the project root.

## 2. Project structure

```
scraping-hub/
├── plan.md
├── client/                          # Frontend — Next.js
│   ├── .env                         # NEXT_PUBLIC_API_URL=http://localhost:8000
│   ├── package.json
│   └── src/
│       ├── app/
│       │   └── page.tsx             # Main page: category selector + run controls
│       ├── components/
│       │   ├── CategorySelect.tsx   # Dropdown/cards for the 6 commodity categories
│       │   ├── RunStatus.tsx        # Live scrape progress (polling the API)
│       │   └── ResultsTable.tsx     # Bids found + downloaded docs summary
│       └── lib/
│           └── api.ts               # Fetch helpers using NEXT_PUBLIC_API_URL
└── server/                          # Backend — FastAPI
    ├── .venv/                       # Virtual environment (all dependencies here)
    ├── .env                         # MFMP_EMAIL=..., MFMP_PASSWORD=...
    ├── requirements.txt
    ├── main.py                      # FastAPI app entrypoint
    ├── app/
    │   ├── config.py                # Loads .env (credentials, paths, base URL)
    │   ├── routes/
    │   │   └── scraper.py           # POST /scrape, GET /scrape/status, GET /categories
    │   ├── services/
    │   │   ├── mfmp_scraper.py      # Selenium automation (login → search → download)
    │   │   └── run_manager.py       # Run state, folder creation, progress tracking
    │   └── data/
    │       └── commodity_codes.py   # The 6 categories mapped to their code lists
    └── documents/                   # Main documents folder (created at runtime)
        └── run_2026-07-07_14-30/    # One folder per run
            ├── bids_export.xlsx     # Excel export from MFMP (stored per run)
            ├── <Bid Title A>/
            │   ├── <Bid Title A>_1.pdf
            │   └── <Bid Title A>_2.docx
            └── <Bid Title B>/
                └── <Bid Title B>_1.pdf
```

## 3. Environment files

**server/.env**
```
MFMP_EMAIL=your_email
MFMP_PASSWORD=your_password
MFMP_LOGIN_URL=https://vendor.myfloridamarketplace.com/login
DOWNLOAD_DIR=./documents
HEADLESS=true
```

**client/.env**
```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## 4. Commodity-code categories (frontend options → codes the scraper enters)

The frontend shows these 6 options; picking one determines the codes entered into MFMP's Commodity Codes filter. High-priority codes are entered first; Medium/Related can be toggled on if results are too few.

| Frontend option | High-priority codes (entered by default) |
|---|---|
| **Design / Graphic / Creative** | 81111512, 82000000, 82140000, 82141500, 82141501, 82141502, 82141505 |
| **Branding / Marketing** | 80141604, 80171702, 82101603, 82101801 (all Medium — category has no High codes) |
| **UI/UX / Web Design** | 43232404, 81111820, 81112103 |
| **Software Development / IT** | 43232400, 43232402, 43232406, 43232407, 81111500, 81111502, 81111503, 81111504, 81111506, 81111507, 81111508, 81111509, 81111510, 81111511, 81111600, 81111704, 81111808, 81111810 |
| **AI / Data / Automation** | 43231511, 43232301, 43232302, 43232307, 43232309, 43232314, 43232615, 80101508, 81112009 |
| **IT Consulting / Staffing / Cloud** | 80101507, 81162000, 81162100 |

The full 105-code list (High + Medium + Related per category) lives in `server/app/data/commodity_codes.py`, and the API exposes it via `GET /categories` so the frontend never hardcodes codes. The UI includes a priority filter (High only / High + Medium / All) so users can widen the search when results are too thin.

## 5. Backend design (FastAPI + Selenium)

**Why browser automation:** MFMP's vendor portal is a JavaScript-heavy app with login, navigation, dropdowns, scrolling, and file downloads — this needs real browser automation, not plain HTTP requests. We use **Selenium** with Chrome/Chromedriver (managed automatically by `webdriver-manager`).

### API endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/categories` | GET | Returns the 6 categories with their codes/priorities for the frontend |
| `/scrape` | POST | Body: `{ category: "ui_ux", priority: "high" }`. Starts a scrape run in the background, returns a `run_id` |
| `/scrape/status/{run_id}` | GET | Live progress: current step, bids found, bids processed, docs downloaded, errors |
| `/scrape/runs` | GET | List past runs and their output folders |

Scrapes run as background tasks so the API responds immediately and the frontend polls status.

### Scraper flow (`mfmp_scraper.py`)

1. **Login** — open `MFMP_LOGIN_URL`, fill email/password from `.env`, submit, wait for the post-login dashboard.
2. **Navigate** — click **Advertisements** in the nav bar.
3. **Advanced Search** — scroll to the search bar, click **Advanced Search** below it.
4. **Commodity Codes** — in the left-side dropdown panel, open the Commodity Codes control and enter each code for the selected category (add codes one by one; the widget likely supports multi-select/typeahead — confirm during implementation).
5. **Search** — click the Search button at the end of the dropdown panel; wait for the results table.
6. **Collect bid rows** — read the results table; capture each row's **Number** (link) and **Title**. Handle **pagination** if results span multiple pages.
7. **Per bid**: click the Number link → bid detail page opens → scroll to the documents section → click each document link to download it. Chrome is configured with a per-run download directory (`download.default_directory` preference, headless downloads enabled); after each click we watch the directory until the file finishes (no `.crdownload` remaining), then move it to `documents/<run>/<bid title>/` and rename it to `<bid title>_<n>.<original ext>` (bid titles are sanitized for filesystem safety; duplicates get an index suffix). Navigate back to the results table and continue.
8. **Export Excel** — back on the results page, click **Export Excel**, wait for the download to complete in the download directory, then move it to `documents/<run>/bids_export.xlsx`.
9. **Finish** — mark the run complete with a summary (bids processed, files downloaded, failures).

### Resilience

- Explicit waits (`WebDriverWait` + expected conditions) on every navigation (no fixed sleeps where avoidable).
- Per-bid try/except: one failing bid logs an error and continues, it does not kill the run.
- Retry (2–3 attempts) on flaky steps: login, document downloads, export.
- Screenshots on failure saved to the run folder for debugging.
- Configurable `HEADLESS` flag so we can watch the browser during development.

### requirements.txt (initial)

```
fastapi
uvicorn[standard]
selenium
webdriver-manager
python-dotenv
pydantic
```

Setup: `python -m venv .venv` → activate → `pip install -r requirements.txt` (`webdriver-manager` downloads the matching Chromedriver automatically at runtime — no manual driver install).

## 6. Frontend design (Next.js)

Single-page dashboard:

1. **Category selector** — the 6 options loaded from `GET /categories`, with the code list previewed for the selected category and a priority toggle (High / High+Medium / All).
2. **Run button** — calls `POST /scrape`, receives `run_id`.
3. **Live status panel** — polls `GET /scrape/status/{run_id}` every few seconds; shows current step (logging in → searching → downloading bid 3/12 → exporting Excel), counts, and errors.
4. **Results summary** — when the run completes: bids found, documents downloaded per bid, path to the run folder and Excel export.

All API calls go through `NEXT_PUBLIC_API_URL` from `client/.env`.

## 7. Implementation order

1. **Scaffold** — `client/` (Next.js) and `server/` (FastAPI + `.venv` + `requirements.txt` + `.env` templates).
2. **Commodity code data** — encode the 105-code document into `commodity_codes.py`; expose `GET /categories`.
3. **Scraper core** — login → Advertisements → Advanced Search → commodity codes → Search (verify selectors against the real site, headful).
4. **Results + bid interiors** — parse table, open each bid, download and rename documents into the run/bid folder structure.
5. **Excel export** — click Export Excel, save into the run's folder.
6. **API + background runs** — `POST /scrape`, status tracking, run listing.
7. **Frontend** — category selector, run trigger, live status, results summary.
8. **Hardening** — pagination, retries, failure screenshots, filename sanitization, end-to-end test per category.

## 8. Open questions / assumptions

- **Selectors are assumptions until verified.** The exact DOM for the nav bar, Advanced Search link, commodity-code widget, results table, and Export Excel button must be confirmed against the live site during step 3 (run headful, inspect, adjust).
- **Commodity-code entry format** — assumed the widget accepts codes typed/selected one at a time; if it's a hierarchical tree picker, the entry logic changes but the category→codes mapping stays the same.
- **Session/MFA** — assumed plain email/password login with no CAPTCHA or MFA. If the portal adds either, we'll need a manual-assist step.
- **Rate limiting / politeness** — small delays between bid openings to avoid hammering the portal.
- **One run at a time** — assumed a single concurrent scrape run is enough (single browser instance); can queue runs if needed.
