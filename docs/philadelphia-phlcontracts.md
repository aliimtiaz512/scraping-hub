# City of Philadelphia — PHLContracts

| Layer | File |
| --- | --- |
| Scraper | `server/app/scrapers/philadelphia/scraper.py` |
| Output layout | `server/app/scrapers/philadelphia/storage.py` |
| Persistence / summary sheet | `server/app/scrapers/philadelphia/export.py` |
| Tables | `server/app/scrapers/philadelphia/models.py` |
| Endpoints | `server/app/scrapers/philadelphia/router.py` (`/philadelphia/*`) |
| Advanced Search form map | `server/app/scrapers/philadelphia/search.py` |
| Migrations | `server/migrations/2026-08-13_add_philadelphia_tables.sql`, `2026-08-13_philadelphia_header_and_items.sql` |
| UI | `client/src/components/PhiladelphiaPanel.tsx`, `PhiladelphiaSearch.tsx`, `PhiladelphiaResults.tsx` |

A run defaults to every open bid — the whole scope the portal publishes, and no
configuration to get wrong. Posting criteria instead drives the portal's own
Advanced Search.

```
POST /philadelphia/scrape        → { run_id }        (?live_preview=true to watch it)
     body: {} or Advanced Search criteria (see below)
GET  /philadelphia/search/fields → the criteria a run accepts, with labels
GET  /philadelphia/scrape/status/{run_id}
GET  /philadelphia/bids?query=&run_id=&limit=&offset=
GET  /runs/{run_id}/download     → the ZIP
```

## Advanced Search

Behind the top bar's "Advanced" link is a search form that, once Document Type
is **Bid Solicitations**, filters on description, item description, buyer,
department, NIGP class, type code, opening date, status and category. A run
given any of these searches instead of walking the open list.

**Both paths end at the same results table**, which is the point: the extractor,
the pager, the detail pages, the attachments, the item text file, the sheet and
the database are all untouched. What changes is only which bids arrive at the
top of the pipeline.

```
POST /philadelphia/scrape
{ "description": "pump", "type_code": "MI", "opening_date_from": "08/01/2026" }
```

Three things worth knowing about driving that form:

1. **Selecting Bid Solicitations is a wait, not a page load.** The page's own
   script returns early for that value and lets PrimeFaces re-render the
   criteria panel over AJAX, so the scraper waits for `#bidSearchForm` to appear.
   `advancedSearchBid.xhtml` is the fallback if the dropdown cannot be driven.
2. **Two controls are filled in by the page itself.** Choosing an Organization
   enables Department and reloads Type Code; choosing an NIGP Class fills NIGP
   Class Item. `search.ORDERED_FIELDS` puts parents before dependants and the
   scraper waits for each dependant to come alive — filling one first would put
   a value into a `disabled` select holding a single empty option, which is a
   filter silently dropped.
3. **A dropdown takes the code or the words.** `"MI"` and `"Micro Purchase"`
   both reach the same option, and `"Bell, Carla"`, `"C.BELL"` and `"carla"` all
   reach the same buyer. That is what lets the dashboard's form stay in the
   words of the person filling it in — and it is why Buyer and NIGP Class are
   text boxes there rather than 130- and 300-option dropdowns copied out of the
   portal.

A criterion the form will not take is **reported, never dropped**: it goes to
the log and to a run warning saying the results are broader than asked for. A
run that silently searched on less than it was given returns the wrong bids and
looks like it worked.

## Two eras of markup, and why the selectors are split

PHLContracts runs on Periscope Holdings' BSO platform, and the page is two
things at once:

* **The shell is JSF/PrimeFaces.** Sign In does not navigate — it calls
  `PF('loginOP').show()` and reveals an overlay whose fields carry JSF's
  colon-joined ids (`homeLoginForm:loginId`). A colon is a CSS pseudo-selector,
  so those are found through `By.ID` (which does not parse them as CSS) and never
  through a hand-escaped selector.
* **Everything behind the login is 2000s table layout.** No ids on rows, no data
  attributes: the list is `table#resultsTable` with `td.tableText-01` cells, the
  detail header is a run of `td.t-head-01` label cells each followed by its
  value, and attachments are anchors whose href is `javascript:editFile('391619')`.

Three consequences worth knowing:

1. **"Left the login page" proves nothing.** The overlay is in the DOM before
   sign-in and the URL never changes, so the login check waits for the seller
   dashboard's own Bids tab — the one thing that only exists once the session is
   real.
2. **The dashboard renders four `table#resultsTable`s** — Request for Revision,
   Bids / Bid Amendments, Open Bids, Closed Bids. The scraper finds the *Open
   Bids heading* and takes the table from there; "the first results table on the
   page" would scrape a different section the day the portal reorders them. A
   test holds this with all four tables present.
3. **Columns are located by header name, then read positionally.** Alternate Id
   is blank on most bids and populated on some, so a fixed index would shift
   every value after it on exactly the rows that carry one.

`View More…` is followed to `bidList.sda?status=Open&category=all`, because the
dashboard's Open Bids table is a five-row preview — the difference between five
bids and every open one. A run that cannot find the link requests that URL
directly rather than settling for the preview, and the list is then walked page
by page (forward links only, visited URLs never re-followed).

## Attachments

The anchors carry no URL to fetch: `javascript:editFile('391619')` calls into the
page, which streams the file through the servlet. There is no address to
construct that is not a guess, so each anchor is clicked and the browser's
download is waited for — the same approach the MyFlorida flow uses on the same
kind of link. Names are read *before* the first click, because clicking
re-renders the page on some bids and an element list read beforehand is a list of
stale handles. A file that never arrives is named in the bid's `document_errors`
and in a run warning, rather than being quietly one short.

## Output

```
CityOfPhiladelphia_Export/
├── Philadelphia_Bids_Summary.xlsx      every open bid, one row, naming its folder
└── Bids_Data/
    ├── B2727750/
    │   ├── bid_items_details.txt       the bid's line items, in plain text
    │   ├── MP_Terms_and_Conditions_B2727750.pdf
    │   └── Consent_Authorization_B2727750.pdf
    └── B2727732/
        └── …
```

**Everything in the archive opens without a developer.** This used to carry an
`Extra_Header_Info.json` per bid, on the reasoning that the header table is a
different set of labels per bid type — a micro purchase carries Type Code and
Informal Bid Flag, a formal solicitation does not — so flattening it into the
sheet would give mostly-empty columns. That reasoning held for the schema and
failed the reader: the people this is built for do not open JSON.

The compromise that keeps both: the three fields worth sorting a spreadsheet on
— **Fiscal Year**, **Procurement / Solicitation Type**, **Pre-Bid Conference
Date / Details** — get columns of their own, and every other published label
lands in one **Additional Header Information** cell as `Label: value` pairs. No
label is lost, and the city adding a row to that table does not need a
migration. `extra_header_data` still holds the table verbatim in the database,
which is what that cell is rendered from when a sheet is rebuilt months later.

The line items were not captured at all before. They now go into the bid's
folder as `bid_items_details.txt` — headings, quantities, units and
specifications as prose. Every bid gets one, including a bid with no item table,
because a folder of PDFs with nothing naming the bid they belong to is the
problem this file exists to solve. It is not counted in **Total Document
Count**, which reports the documents the *city* published.

The summary's `Folder` column holds the path *inside the archive*
(`Bids_Data/B2727750`), so a row points at a real location in the unpacked ZIP.

The sheet is written to disk **before** the database is touched: the deliverable
is an archive whose root holds it next to the documents it indexes, so it has to
exist at packaging time whatever the database is doing.

## The table

`city_of_philadelphia_bids` is keyed on `bid_number` — **one row per bid, not per
sighting**. The Open Bids list is a live set: the same bid is in it every day
until it closes, and what a reader wants is its current state, not one row per
time it was seen. So a second run updates the row it already holds.

Two things a repeat sighting does *not* overwrite:

* `first_seen_at`, which is the point of having it (`scraped_at` moves).
* `extra_header_data` / `file_paths` when this run captured none — a detail page
  that failed to load today must not erase what it gave yesterday.

`run_id` is `ON DELETE SET NULL`, so clearing an old run's history does not
delete bids that are still open.

## Setup

```ini
# server/.env
PHILA_EMAIL=your_email_here
PHILA_PASSWORD=your_password_here
```

The tables are created automatically by `create_all` on a fresh database; on an
existing one run
`psql "$DATABASE_URL" -f server/migrations/2026-08-13_add_philadelphia_tables.sql`.

## Line items, and the two shapes they come in

PHLContracts prints line items two ways, and the scraper reads both.

**Blocks** are what live runs meet, and they are not a grid — there is no header
row at all:

```
Item: 1  072-08
42831-002-156  FREIGHTLINER 114SD CHASSIS AS PER DFS SPEC 25026CNGb.25
Quantity: 2   UOM: EA   Unit Cost: $284,000.00
```

A cell naming the item and its NIGP class-item, beside a cell holding the
commodity code and the description, with quantity and unit as labelled text
underneath. `Item:` at the *start* of a cell opens a block and everything until
the next one belongs to it.

**Grids** are the ordinary header-and-columns table, read by column name.

The grid header test is deliberately strict, and the reason is a bug worth not
repeating. An earlier version matched `(item|line)` anywhere in one cell and any
of `qty|unit|spec|…` anywhere in another — which a *data* row satisfies:
"Item: 1 072-08" begins with "item", and "…AS PER DFS SPEC 25026CNGb.25"
contains "spec". The first data row was taken as the header row, and a 45-bid
run reported zero items for every bid. A header cell now has to **be** a label
rather than contain one: matched whole, and under forty characters, because no
column heading is sixty characters of description.

The run log names which strategy read the page (`grid` or `blocks`). "blocks" on
a page that should be a grid is the first sign the portal has changed shape.
