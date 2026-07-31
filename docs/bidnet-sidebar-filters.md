# BidNet Direct — niche-driven sequential search + sidebar filters

How a niche's keywords are searched one at a time, and how the frontend's
filter choices reach BidNet Direct's search sidebar.

**Unchanged:** the login flow (`BidnetScraper.login`) and the mechanics of a
single keyword search (`BidnetScraper.search` — type into the box, click the
button) are untouched. What changed is *what* gets searched (a niche's keywords,
from the database, one at a time) and *where the output lands* (one folder, one
spreadsheet). Filters are applied after each keyword's search has run, narrowing
that search's own result set.

## Where each piece lives

| Layer | File |
| --- | --- |
| Niche catalog (keywords, DB-backed) | `server/app/scrapers/bidnet/niches.py`, `niche_models.py` |
| Filter catalog, request model, validation | `server/app/scrapers/bidnet/filters.py` |
| Selenium driver for the sidebar | `server/app/scrapers/bidnet/sidebar.py` |
| Option discovery ("View All" harvest) | `server/app/scrapers/bidnet/discovery.py` |
| Scrape wiring | `server/app/scrapers/bidnet/scraper.py` |
| Endpoints | `server/app/scrapers/bidnet/router.py` |
| UI | `client/src/components/BidnetNicheSelect.tsx`, `BidnetFilters.tsx`, `BidnetPanel.tsx` |

The console is two cards: **Niche** (a single dropdown) and **Filters** — Status
always visible, every other panel a collapsed row showing its current selection,
one open at a time.

## 1. Element targeting

### The one shape every list panel shares

The six list filters are structurally identical, which is why one generic driver
handles all of them:

```html
<div id="panel{SECTION}-body" data-filter-section="{SECTION}" class="auto-search filterPanel mets-panel-body">
  <input id="{FIELD}" name="{FIELD}" class="filterHiddenField" type="hidden" value="{csv of selected values}">
  <ul>
    <li data-filter-item-value="{VALUE}">
      <element title="{FULL LABEL}">
        <span class="mets-checkbox" data-filter-item-value="{VALUE}">
          <input type="checkbox" id="g_NNN" data-filter-item-value="{VALUE}">
          <span class="inputText">{LABEL}</span>
        </span>
        <span class="filter-item-count">{N}</span>
      </element>
    </li>
  </ul>
  <div class="linksPanel"><a id="viewAll{SECTION}" href="…/advanced/lightbox?section={SECTION}">View All</a></div>
</div>
```

**The `g_NNN` ids are never targeted.** They are regenerated on every render
(`g_347`, `g_353`, `g_401`…), so any selector built on them breaks on the next
postback. The stable handles are `data-filter-item-value`, the panel body id, and
the hidden field id.

### Per-filter selector map

| UI filter | API name | `data-filter-section` | Panel body | Hidden field (`name` = `id`) | Option selector |
| --- | --- | --- | --- | --- | --- |
| Status | `status` | — (radio group) | `.statusWrapper` | — | `input[name='status'][value='OPEN'\|'CLOSED'\|'AWARD']` |
| NIGP Codes | `nigp_categories` | `categories.NIGP_SS` | `#panelcategoriesNIGP_SS-body` | `#categorySelectionNIGP_SS` (`categorySelection[NIGP_SS]`) | `#panelcategoriesNIGP_SS-body li[data-filter-item-value='…'] input[type=checkbox]` |
| Organization | `organizations` | `buyerOrganizationId` | `#panelbuyerOrganizationId-body` | `#buyerOrganizationId` | same shape |
| Location | `locations` | `regionId` | `#panelregionId-body` | `#regionId` | same shape |
| Purchasing Group | `purchasing_groups` | `solicitationPurchasingGroupId` | `#panelsolicitationPurchasingGroupId-body` | `#solicitationPurchasingGroupId` | same shape |
| Solicitation Type | `solicitation_types` | `solicitationType` | `#panelsolicitationType-body` | `#solicitationType` | same shape |
| General Requirements | `general_requirements` | `buyerReqsCodes` | `#panelbuyerReqsCodes-body` | `#buyerReqsCodes` | same shape |
| Publish Date | `published_date` | `publishedDate` | `#panel_publishedDate-body` | see date table | see date table |
| Closing Date | `closing_date` | `closingDate` | `#panel_closingDate-body` | see date table | see date table |

Two quirks worth knowing:

* **`#regionId` is not empty when unselected** — it ships the literal sentinel
  `[null-null]{}`. Clearing Location means writing that string back, not `""`.
  Encoded as `Section.empty_value`.
* **Purchasing Group arrives fully selected** — its hidden field ships all 52 ids
  and every visible checkbox carries `checked="checked"`. It is the only panel
  where the user *unselects*, so `default_all=True` and an omitted request field
  means "leave all 52".

### Date panels

Both panels share one shape, keyed by `{SECTION}` = `publishedDate` | `closingDate`:

| Control | Selector | Posted as |
| --- | --- | --- |
| Mode checkbox | `#{SECTION}Check{TYPE}` | `{SECTION}.dateType` = `SINCE_LAST_LOGIN` \| `DAY` \| `WITHIN` \| `RANGE` |
| Specific day | `#{SECTION}DAY` (visible) + `#{SECTION}DAY_hidden` | `{SECTION}.localDay` |
| Within period | `#{SECTION}WITHIN` (`<select>`) | `{SECTION}.within` = `DAY`\|`WEEK`\|`MONTH`\|`YEAR` |
| Range start | `#{SECTION}RANGE1` + `#{SECTION}RANGE1_hidden` | `{SECTION}.localRangeStart` |
| Range end | `#{SECTION}RANGE2` + `#{SECTION}RANGE2_hidden` | `{SECTION}.localRangeEnd` |
| Apply | `#{SECTION}SearchButton` | — |
| Clear | `#{SECTION}ClearLink` | — |

`SINCE_LAST_LOGIN` exists **only** on Published Date; the Closing Date panel has
no such checkbox. Date text is `mm/dd/yyyy` (the panel's jQuery datepicker is
configured `dateFormat: "mm/dd/yy"`).

Three DOM facts drive how these are set:

1. The text inputs are `readonly` **and** `disabled` until their checkbox is
   ticked — they are datepicker-driven, so `send_keys` is not an option. The
   driver clears both flags, assigns `.value`, mirrors it into the `_hidden`
   twin (that is what the form actually posts), and dispatches `change` by hand
   because the field's behaviour hangs off `onchange="updateDateStatus(…)"`.
2. `#{SECTION}SearchButton` ships `class="… disabled"` / `aria-disabled="true"`
   and is only enabled by the page's own validation, which never fires for a
   programmatic assignment. The flags are cleared before the click.
3. Each real `<input>` is visually replaced by a styled `<span class="checkbox">`
   / `<span class="radio">`, so a native Selenium click can land on the overlay.
   Every toggle goes through `execute_script("arguments[0].click()")`, which
   reaches the input and still fires the page's handlers.

### Reading the options ("View All")

Each panel renders only its ~12 highest-count options inline. The rest are behind
`#viewAll{SECTION}`, an AJAX `POST` that renders
`/private/supplier/solicitations/search/advanced/lightbox?section={SECTION}` into
`#savedSearchDialogContainer`. `SidebarDriver.harvest()` clicks each one, waits
for `[data-filter-item-value]` nodes to appear in that container, reads
`{value, label}` out of them, and closes the dialog. `POST /bidnet/filters/refresh`
runs this as a background browser pass and writes the result to
`server/data/bidnet_filter_options.json`, which `GET /bidnet/filters` merges over
the seeded catalog.

Until that pass has run, four sections are flagged `partial: true` in the API
response and the UI offers a "Refresh options" button on them. Location and
Purchasing Group are **not** partial:

* **Location** is generated from the portal's own id sequence — `regionId = 6n + 13`
  over the 50 states plus DC, alphabetically. All twelve ids the sidebar ships
  inline fit it exactly (California 5→43, Colorado 6→49, Florida 10→73,
  Georgia 11→79, Illinois 14→97, Michigan 23→151, New Jersey 31→199,
  New York 33→211, Oklahoma 37→235, Rhode Island 40→253, Tennessee 43→271,
  Texas 44→277), so all 51 are derived rather than transcribed. Entries past
  those twelve are *derived, not observed*; a discovery pass overwrites them.
* **Purchasing Group** is seeded whole from the hidden field's own 52 ids. Only
  the twelve rendered inline have labels; the rest read as
  `Purchasing group {id}` until discovery names them.

## 2. Backend workflow

```
POST /bidnet/scrape  { niche: "ai_analytics", filters: { status, locations, …, closing_date } }
  → filters.validate_request()          reject unknown ids / incomplete dates (400)
  → niches.get_niche / keywords_for     resolve the niche's keywords from the DB (400 if unknown/empty)
  → run_manager.create_run(...)         the run records the niche and its filters
  → BidnetScraper(run_id, keywords, filters, niche_label)

login()                                 once per run
for each keyword of the niche:          SEQUENTIAL — one keyword, one search
    ensure_logged_in()                  re-login if the session expired mid-run
    search(keyword)                     types into #solicitationSingleBoxSearch,
                                        clicks #topSearchButton, waits for jQuery idle
    result_count() == 0 ? -> continue   FAST-FAIL: skip the keyword entirely
    filter_member_agency()
    apply_sidebar_filters()             ← SidebarDriver.apply(filters)
    collect_links()                     paginate, accumulating links
for each DISTINCT link:                 deduplicated across every keyword
    process_bid(link, run_folder)       scrape fields + download documents
_write_master_excel()                   one spreadsheet for the whole run
```

### Niches and keywords

A run searches **one niche**. Its keywords live in `bidnet_niche_keywords`,
seeded at API startup from `NICHES` in `app/scrapers/bidnet/niches.py` (the
source of truth — edit there and restart). `GET /bidnet/niches` returns only
`{key, label, slug, keyword_count}`: the terms never reach the browser, and the
scrape request carries a niche key, nothing else.

**One keyword, one search.** The client's taxonomy guide supplies "copy-paste
search strings" like
`("graphic design" OR "ADA compliant") AND ("annual report" OR "signage")`.
Those are deliberately **not** used — a combined boolean query only returns
solicitations matching every AND-group, a fraction of what the terms find
individually. The strings are decomposed into their component terms and searched
one at a time in the same session, then merged.

Multi-word terms are stored quoted (`"graphic design"`) so BidNet matches the
phrase; the search box's own help documents `AND`, `OR` and parentheses, and the
guide quotes every phrase. A quoted phrase is still one search term.

The guide's NIGP/UNSPSC codes are recorded in each niche's `notes` for
traceability but are **not** searched: the keyword box is full-text, and BidNet's
NIGP sidebar filter keys off internal ids (`112450`), not published class-item
numbers (`965-46`).

### Output

One project folder per run — `Bidnetdirect <niche-slug> (<timestamp>)` — holding
a per-bid subfolder of documents for every solicitation, and one master
spreadsheet at its root. There is no per-keyword or per-tier split: a run is a
single niche.

The master sheet is named with `core.exports.excel_name`, the same name the
packaging step gives its DB-regenerated copy, so `build_zip` recognises it as
already present and the ZIP ships **exactly one** spreadsheet — the database's
version when Postgres is reachable, the scraper's on-disk one when it is not.
One row per solicitation, with every keyword that surfaced it comma-joined in
`Matched Keyword`.

### Zero-result fast-fail

A niche's keywords routinely match nothing on a given day — in one live sample,
three of four did. Before this check, an empty search cost a grouping click plus
a **60-second** element wait for rows that were never coming, and then logged a
misleading "search failed" error.

The portal answers the question directly. Each result-group tab carries its own
count:

```html
<div class="searchContentGroupContainer" search-content-group-id="2085061601">
  <span class="solicitationCount">1,848</span> … Member Agency Bids
```

`result_count()` reads `.solicitationCount` from the Member Agency group — 0 when
nothing matched. A zero sends the loop straight to the next keyword: no grouping
click, no sidebar filters, no pagination, no row waits.

Two DOM facts make the naive checks wrong, both established by inspecting the
live portal:

1. **The "No results match your criteria." row is always in the DOM.** It is a
   template row; only the `visible` class distinguishes the states
   (`mets-table-row-empty visible` vs `mets-table-row-empty`). Testing for the
   element's *existence* reports every search as empty. Counting `.visible` ones
   page-wide is no good either — other tables on the page contribute their own
   (2 for a results-rich search, 3 for an empty one).
2. **The count is stale until the search's AJAX lands.** Measured immediately
   after `search()` returns, a keyword with 524 hits still showed the *previous*
   search's `1,848` with `jQuery.active == 1`, settling ~0.5s later. Reading
   early gives a confidently wrong answer, so `_await_ajax_idle()` waits for
   `jQuery.active == 0` first — which also replaced a blind 5-second sleep in
   `search()`.

An unreadable count returns `None` and the keyword is processed normally:
skipping one we could not read would silently lose bids, so the check fails open.

Skipped keywords are collected on the run as `keywords_without_results`, with a
warning naming them; a niche where *every* keyword misses sets `no_results`.

### Timeouts

A niche of ~20 keywords is a long sequential run, so the waits are generous
(`scraper.py`): `ELEMENT_TIMEOUT` 60s, `DETAIL_TIMEOUT` 45s, `PAGINATION_TIMEOUT`
30s, `DOC_DOWNLOAD_TIMEOUT` 90s, `SEARCH_SETTLE_SECONDS` 5s; shared
`WAIT_TIMEOUT` 30→60s and `DOWNLOAD_TIMEOUT` 120→300s in `base_scraper.py`;
`POSTBACK_TIMEOUT` 30→60s in `sidebar.py`.

Timeouts alone are not enough: BidNet expires the session partway through a long
run, which surfaces as a redirect to the login page rather than as a timeout.
`ensure_logged_in()` checks for the post-login menu before every keyword and
signs in again if it has gone, recording a note on the run.

### How a selection becomes a form submission

Every list panel is `class="auto-search filterPanel"`: ticking one checkbox fires
a **full search postback**. Selecting twelve NIGP codes by clicking would be
twelve round-trips, and any option outside the inline top-12 is not clickable at
all without first opening its lightbox.

So the primary path writes the selection straight into the authoritative control
the page itself submits — the `.filterHiddenField` inputs — and submits the search
form **once**:

| Request field | Written to hidden input | Value written |
| --- | --- | --- |
| `nigp_categories: ["112450","112716"]` | `categorySelection[NIGP_SS]` | `112450,112716` |
| `organizations: ["416971005"]` | `buyerOrganizationId` | `416971005` |
| `locations: ["49","211"]` | `regionId` | `49,211` (empty ⇒ `[null-null]{}`) |
| `purchasing_groups: [ …51 ids ]` | `solicitationPurchasingGroupId` | comma-joined ids |
| `solicitation_types: ["RFP_F"]` | `solicitationType` | `RFP_F` |
| `general_requirements: ["INSURANCE_REQUIRED"]` | `buyerReqsCodes` | `INSURANCE_REQUIRED` |

The page tags each postback with which filter action triggered it
(`actionFilterCriterion` / `actionFilterValue` / `actionFilterSelected`). A bulk
write is not one action, so the criterion and value are cleared and
`actionFilterSelected` is set to `true` — the submit is a plain "apply the current
criteria" request.

The submit itself is `form.requestSubmit()`, **not** `form.submit()`: the latter
skips the form's own submit event by spec, so BidNet's jQuery handlers (which
normalise the criteria before posting) would never run. `submit()` remains only as
a fallback for a browser without `requestSubmit`.

`status` and the two date panels are **always** driven by clicking (radio, and
checkbox+control+Apply). They are single interactions, so there is nothing to
batch, and clicking uses the page's own handlers.

### Verification and fallback

After the bulk submit the driver re-reads every hidden field and compares it to
what was requested. If any selection did not survive the round-trip — which is
what would happen if the portal rebuilds criteria from something other than these
inputs — it falls back to `_apply_by_click`: one postback per changed inline
checkbox, using nothing but the page's own handlers. That path is slower and can
only reach inline options, so values it cannot apply are reported into the run's
error list rather than silently dropped.

**The bulk path is the one to watch on the first live run.** It is derived from
the sidebar markup, not from an observed session — the fallback exists precisely
because it may not hold. `run.filters_applied.strategy` says which path ran
(`"bulk"` or `"click"`), and the run's errors carry the reason for a fallback.

### Request contract

```jsonc
POST /bidnet/scrape
{
  "keywords": ["Construction AND Demolition", "Roofing"],  // [] = server catalog
  "filters": {
    "status": "OPEN",                                  // OPEN (default) | CLOSED | AWARD
    "nigp_categories":      ["112450"],                // [] = no constraint
    "organizations":        ["416971005"],             // [] = no constraint
    "locations":            ["49", "211"],             // [] = no constraint
    "solicitation_types":   ["RFP_F"],                 // [] = no constraint
    "general_requirements": ["INSURANCE_REQUIRED"],    // [] = no constraint
    "purchasing_groups":    null,                      // null/omitted = keep all 52 ticked
    "published_date": { "type": "WITHIN", "within": "WEEK" },
    "closing_date":   { "type": "RANGE", "range_start": "08/01/2026", "range_end": "09/30/2026" }
  }
}
```

An omitted body is valid and reproduces the pre-filter behaviour exactly: the
server's keyword catalog, Open Solicitations, all purchasing groups, nothing else
constrained.

Unknown option ids are **rejected** rather than passed through — a typo'd id would
otherwise produce a filter the portal ignores, and a run that quietly searched
something other than what was asked for is worse than one that refuses to start.

### Interaction with the existing close-date filter

`app/core/closing_filter` already drops solicitations closing sooner than
`MIN_DAYS_UNTIL_CLOSE`, applied per-bid *after* the detail page is scraped. The
sidebar Closing Date filter is applied earlier, at the portal. They compose: the
sidebar narrows what is collected, the close-date filter still drops anything
too near its deadline from whatever survives.
