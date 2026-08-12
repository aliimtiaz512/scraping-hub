# BidNet Direct — niche-driven sequential search + sidebar filters

How a niche's search terms — its keywords, then its NIGP codes — are searched
one at a time, and how the frontend's filter choices reach BidNet Direct's search
sidebar.

**Unchanged:** the login flow (`BidnetScraper.login`) and the mechanics of a
single search (`BidnetScraper.search` — type into the box, click the button) are
untouched. What changed is *what* gets searched (a niche's keywords and then its
NIGP codes, from the database, one term at a time), *when the filters are
applied* (once per run, before the first term — see below), and *where the output
lands* (one folder, one spreadsheet, one row per solicitation however many terms
found it).

## Where each piece lives

| Layer | File |
| --- | --- |
| Niche catalog (keywords + NIGP codes, DB-backed) | `server/app/scrapers/bidnet/niches.py`, `niche_models.py` |
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
no such checkbox.

**The two fields hold different formats, and this matters more than anything
else on this panel.** The visible input is `mm/dd/yyyy` (the jQuery datepicker is
configured `dateFormat: "mm/dd/yy"`); its `_hidden` twin — the one carrying the
`name` the form actually posts — is **ISO `yyyy-mm-dd`**:

```html
<input id="publishedDateRANGE1"        value="08/04/2026">        <!-- display -->
<input id="publishedDateRANGE1_hidden" value="2026-08-04"         <!-- posted  -->
       name="publishedDate.localRangeStart">
```

Writing the US format into the twin posts a date the server cannot parse: the
range collapses to empty, every keyword returns zero, and the panel still looks
correctly filled in. That was a real bug — a whole niche exported nothing while
the sidebar showed the window the user asked for. `_set_date_input` therefore
sets the visible field through the widget (`$(el).datepicker('setDate', …)`,
whose `onSelect` syncs the twin), writes ISO into the twin as a backstop, and
calls `updateDateStatus(section)` — the page's own validator, and what lifts the
Apply button out of its initial disabled state.

Four DOM facts drive how these are set:

1. The text inputs are `readonly` **and** `disabled` until their checkbox is
   ticked — they are datepicker-driven, so `send_keys` is not an option. The
   driver clears both flags, assigns `.value`, mirrors it into the `_hidden`
   twin (that is what the form actually posts), and dispatches `change` by hand
   because the field's behaviour hangs off `onchange="updateDateStatus(…)"`.
2. `#{SECTION}SearchButton` ships `class="… disabled"` / `aria-disabled="true"`
   and is a jQuery `commandButton` constructed with `enabled: "false"`, so
   clearing the CSS flags is not on its own proof the click will act — the
   widget latched its state at construction. The page enables it through
   `updateDateStatus(section)`, which is why that is called after the writes;
   the flags are cleared and the click still forced as a fallback, and a button
   that was still disabled at that point is logged.
3. The panel validates in place: `<div class="message151 error hidden">Ending
   date must be greater or equal to the starting date.</div>`, unhidden when it
   fires. It is read before Apply, so a refusal is reported as its own reason
   rather than as an unexplained empty result set.
4. Each real `<input>` is visually replaced by a styled `<span class="checkbox">`
   / `<span class="radio">`, so a native Selenium click can land on the overlay.
   Every toggle goes through `execute_script("arguments[0].click()")`, which
   reaches the input and still fires the page's handlers.

### The Keywords panel (Excluded Keywords)

The one free-text panel. No catalog, no hidden field — a textarea and the
panel's own Apply button:

| Control | Selector |
| --- | --- |
| Excluded keywords | `#excludedKeywords` (`name="excludedKeywords"`, inside `#panel_keywords-body`, `data-filter-section="keywords"`) |
| Apply | `#keywordsSearchButton` |
| Clear | `#clearIncludedExcludedKeywords` |

Two facts, both measured against the live portal rather than assumed:

1. **The panel is collapsed by default** — the textarea reports
   `offsetParent: null`, so `send_keys` raises "element not interactable". The
   value is assigned through the DOM and followed by `input`/`change` events,
   which reaches it whether or not the accordion happens to be open.
2. **The box is a boolean query, not a list.** Against a search returning 1371
   solicitations:

   | typed into the box | results | excluded |
   | --- | --- | --- |
   | `software` | 1292 | 79 |
   | `training` | 1316 | 55 |
   | `software training` | 1369 | 2 |
   | `software, training` | 1369 | 2 |
   | `software\ntraining` | 1369 | 2 |
   | **`software OR training`** | **1251** | **120** |

   Spaces, commas and newlines all collapse into a single *phrase*, which
   almost nothing matches — so a multi-term exclusion entered any of those ways
   silently filters nothing while looking like it worked. The frontend accepts
   commas/newlines because that is how people write lists;
   `SidebarFilterRequest.excluded_keywords_expression()` translates them into
   the `OR` form, quoting multi-word terms so the phrase boundary is explicit
   (`"fire alarm" OR training`, which the portal treats identically to the
   unquoted form).

Applied last in `SidebarDriver.apply()`, so the exclusions bite on whatever the
other panels narrowed to. Blank means the panel is never touched. The wait after
Apply is anchored on a current results row going **stale**, not on rows being
present: the old rows stay in the DOM until the postback swaps them, so a
presence wait returns instantly and the caller reads the previous page — which
is exactly what happened when re-reading the count straight after an apply.

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
open_filtered_session()                 ONCE per run — the sidebar, incl. dates:
    reset_search_state()                a clean search page
    search("")                          empty search → the sidebar renders
    apply_sidebar_filters()             ← SidebarDriver.apply(filters)
for each keyword of the niche:          SEQUENTIAL — one keyword, one search
    ensure_logged_in()                  re-login if the session expired mid-run
    _ensure_filters_live()              re-applies ONLY if something navigated
    search(keyword)                     types into #solicitationSingleBoxSearch,
                                        clicks #topSearchButton, waits for the
                                        previous results to go STALE, then jQuery idle
                                        — the session's filters ride along
    _ensure_first_result_page()         back to page 1 if the last harvest left
                                        the results deeper in
    result_count() == 0 ? -> continue   FAST-FAIL: skip the keyword entirely
    filter_member_agency()
    confirm_filters_active(keyword)     ← SidebarDriver.state_intact(filters),
                                        read-only; re-applies only on drift
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

### NIGP codes are searched too, through the same box

Each niche owns a second list, `nigp_codes` — the guide's NIGP class-item and
UNSPSC numbers — and a run searches them one at a time in
`#solicitationSingleBoxSearch`, exactly as it searches keywords, **after** every
keyword of that niche:

| Niche | Codes |
| --- | --- |
| Graphic Design | 965-46, 915-48, 915-22, 915-09, 82131603 |
| Commercial Printing | 966-00, 966-18, 966-28, 966-55, 966-86 |
| Custom Software | 920-40, 920-45, 920-03, 918-29, 81111500 |
| Artificial Intelligence | 920-04, 918-30, 920-24, 81111508 |
| Printed Circuit Board | 287-54, 287-00, 936-25, 32101501 |

Both kinds live in `bidnet_niche_keywords`, told apart by `kind`
(`keyword` | `nigp`) and ordered by `sort_order`, which is what puts every
keyword ahead of every code. `niches.search_terms_for()` returns the queue and
the scraper iterates it; `SearchTerm.kind` is what the logs name and what the
`[SEARCH EXECUTING]` line reports. An existing database needs
`migrations/2026-08-11_add_bidnet_niche_kind.sql`; without it the catalog is read
from `niches.py` instead and the run says so.

This is **not** the portal's NIGP sidebar filter and does not replace it. That
filter keys off BidNet's internal ids (`112450`) and narrows by how the *portal*
classified a solicitation; searching `965-46` as text finds the notices that
quote the code in their own words. Expect codes to return nothing more often
than keywords do — a term that matches nothing costs one search, the same as any
keyword that misses.

### One bid, one row — the deduplication engine

Searching a sector's keywords and then its codes finds the same solicitation
repeatedly by design, so identity is tracked for the whole run and a bid is
opened, downloaded and exported **once**. Two rounds, because a solicitation has
two identities and they can disagree:

1. **By solicitation id, as links are collected** (`_bid_key`). The trailing path
   segment, not the URL: BidNet serves the same bid as
   `/interception/view-notice/<id>` *and*
   `/interception/open-solicitation/<id>?target=view`, so comparing URLs would
   make those two bids — opened twice, downloaded twice. This is the round that
   saves the work.
2. **By reference number, once the detail page has been read** (`_claim_bid`,
   `_seen_bid_ids`). Nothing reaches the master list, the spreadsheet or the
   database without passing it. It catches what the first cannot: two different
   link ids that turn out to carry one reference number.

A repeat sighting is not simply discarded — the term that found it again is
added to the kept row's `Matched Keyword`, so the export still shows that a bid
was reached by both a keyword and a code. A bid whose reference number could not
be read falls back to its link id rather than deduplicating on an empty string,
which would merge every failed extraction in the run into one row.

The per-search line says what each term actually contributed:

```
[SEARCH EXECUTING]: (3/27) Niche: Graphic Design | Input Type: NIGP CODE | Term: "965-46"
 ├── [PORTAL DETECTED]: 4 matching bid(s) reported by the Member Agency group.
 ├── [PARSED SUCCESS]: 4/4 row(s) converted to links.
 ├── [POST-FILTER]: 4 retained (0 dropped: 0 unreadable, 0 repeated across pages).
 └── [RESULT]: 4 total bids found (1 new, 3 duplicates skipped). 12 unique solicitation(s) queued.
```

and the run closes with a `[DEDUPLICATION]` line — unique ids, repeat sightings
across terms, and records dropped after extraction — which is the only place a
niche whose codes never add anything new is visible, since the export by
construction contains no duplicates at all.

### Output

One project folder per run — `Bidnetdirect <niche-slug> (<timestamp>)` — holding
a per-bid subfolder of documents for every solicitation, and one master
spreadsheet at its root. There is no per-keyword or per-tier split: a run is a
single niche.

The master sheet is named with `core.exports.excel_name`, the same name the
packaging step gives its DB-regenerated copy, so `build_zip` recognises it as
already present and the ZIP ships **exactly one** spreadsheet — the database's
version when Postgres is reachable, the scraper's on-disk one when it is not.
One row per solicitation, with every term that surfaced it — keywords and NIGP
codes alike — comma-joined in `Matched Keyword`.

### Why each search waits for the previous results to go stale

Both of the obvious waits pass on the *previous* keyword's page:

* `.searchContentGroupContainer` is already visible and is never removed, so a
  visibility wait returns before the new search has even been sent.
* `jQuery.active == 0` is still true in the moment between the click and the
  request leaving, so an idle-wait can pass on the pre-search page.

The symptom was a run that spent real time on a niche's first keyword and then
raced through the rest returning nothing, while the same searches by hand
returned results. `result_count()` was reading the previous keyword's tab: a
stale `0` skipped the keyword as empty, and a stale non-zero re-collected links
that deduplication then swallowed — indistinguishable from outside.

So `search()` holds a node from the current group container, submits, and waits
for it to go **stale** before believing anything on the page. If the portal
answers without re-rendering (it occasionally does for an identical query) that
is a warning and a settle, never a failure — a skipped keyword is the outcome
being fixed.

Pagination follows the same rule — the next-page wait is on the first row's href
actually *changing*, not on rows being present, so a slow page is no longer read
as the end of the list.

### The filters are applied once, then checked — not re-applied per keyword

The sidebar belongs to the **search form**, not to a keyword's results. Applied
once, its panels are posted with every later search of the same session, so the
run sets them up front — before the first keyword — and every keyword below is
searched with the Published Date window already in force.

What that replaced: each keyword began with `reset_search_state()`, and a reload
clears the panels, so the whole sidebar had to be re-driven afterwards. For the
date panels that is four postbacks each (tick the mode checkbox, write the
fields, press `#publishedDateSearchButton`, wait out the reload) times twenty-odd
keywords, on a portal where every postback is a full page round-trip. It also
left a window in which a keyword's results existed *before* its date filter had
landed — the harvest reading a page the filter had not reached yet is a bug this
file already documents twice.

The reload was doing two other jobs, and those are now done directly:

* **the keyword box** — `search()` clears it through Selenium *and* through the
  DOM (`value=''` plus an `input` event) before typing the next term, because
  the portal binds its own model on input events; a half-cleared box searches
  `alpha beta` instead of `beta`.
* **the results page** — `collect_links()` walks to the *last* page of every
  keyword's results, so `_ensure_first_result_page()` reads the pagination bar
  and takes them back to page 1 before the next harvest. Harvesting from page 7
  would silently lose pages 1-6 and report a smaller, entirely plausible number.

Persistence is **verified, not assumed**. `SidebarDriver.state_intact(request)`
re-reads the status radio, every panel's hidden field and both date panels — one
read-only round-trip, no clicks and no postback — after each keyword's search.
Intact is the normal answer and costs nothing; drift is reported with the panel
named and repaired by re-applying the sidebar to that keyword's own results,
which is the old behaviour, now paid for only where it is needed. The keywords
that needed it land on the run as `filters_reapplied_keywords`.

Anything that navigates invalidates the session state explicitly: `login()`
clears `_filters_live` (a re-login lands on the dashboard, which has no sidebar),
and so does a keyword that failed mid-page. The next keyword re-establishes the
filters before it searches.

The per-keyword date tally (`dates_applied_keywords` / `dates_missed_keywords`)
is unchanged in meaning — it is now fed by reading the panels back rather than by
re-driving them.

One consequence worth knowing when reading a run: a keyword reporting **0 bids
is 0 under the run's filters**, not proof the term matches nothing on BidNet.
The search the portal answered was already narrowed, so there is no longer an
unfiltered count per keyword to compare against. The before/after comparison
still exists once, on the session's own results page.

### The 7-day closing-date filter

`APPLY_CLOSE_DATE_FILTER` in `scraper.py` is **False for the testing phase**: a
run keeps every solicitation the portal returned, whatever its closing date, so
its count is comparable with a manual search's. The rule, its tallies and its
reporting are all still in place — flipping the flag restores them, and nothing
else changes. While it is off the run does **not** report
`min_days_until_close`, so the console shows no closing-date note for a filter
that did not run.

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

### Zero data loss

Every solicitation the run opens reaches the spreadsheet. Each record carries a
`Status`:

| Status | Meaning |
| --- | --- |
| `OK` | every expected field was read |
| `PARTIAL_DATA` | some fields read, but reference number or title missing |
| `EXTRACTION_FAILED` | the detail page yielded nothing at all |

A page that renders nothing is **reloaded once** before being flagged — a single
slow load is the usual cause. Anything not `OK` is logged with its URL, added to
the run's errors, and exported with a `Detail URL` column so it can be chased by
hand. The console shows a "Not readable" / "Partial" badge on those rows.

This closes a real data-loss path. `generate_excel_from_records` used to skip any
record without a reference number, while its caller logged `len(records)` — the
*unfiltered* count. A run that scraped 12 bids logged "wrote 12 bids" into a file
holding 10, and the two dropped were exactly the ones worth seeing. The writer
now writes every record and returns its true count, which the caller logs and
cross-checks, raising a run error if the two ever disagree.

`save_bids` keeps blank-reference records too (the unique constraint is on
`(run_id, reference_number)` and Postgres treats NULLs as distinct). A genuine
duplicate reference is still collapsed, but logged with the reference rather than
skipped in silence.

Every run prints a reconciliation line before anything is saved:

```
[SUMMARY] run <id> | Scraped: 19 | Fully extracted: 10 | Failed/Fallback: 2 |
          Final Export Count: 12 | Skipped (closing soon): 7
```

with an explicit `MISMATCH` error if collected − skipped ≠ exported.

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

`app/core/closing_filter` drops solicitations closing sooner than
`MIN_DAYS_UNTIL_CLOSE`, applied per-bid *after* the detail page is scraped. The
sidebar Closing Date filter is applied earlier, at the portal. They compose: the
sidebar narrows what is collected, and the close-date filter drops anything too
near its deadline from whatever survives.

**Currently only the sidebar half is live** — `APPLY_CLOSE_DATE_FILTER` is False
for the testing phase (see "The 7-day closing-date filter" above), so the
per-bid rule drops nothing. The sidebar's own Closing Date panel is unaffected
and still narrows at the portal if the frontend sets it.
