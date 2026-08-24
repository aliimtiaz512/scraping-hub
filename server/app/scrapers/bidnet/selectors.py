"""Every DOM handle the BidNet Direct scraper uses, in one place.

**Measured, not assumed.** Each entry below was probed against a live logged-in
results page (`graphic design`, 25 rows) and carries what the probe reported, so
a future change can be checked against evidence instead of memory.

Two rules this file exists to enforce:

* **No `g_NNN` ids.** The portal regenerates them on every render (`g_427`,
  `g_428`, `g_435`…), so anything built on them breaks at the next postback. The
  stable handles are semantic ids, `name` attributes, `data-*` attributes, and
  href *paths*. The results-per-page `<select>` is the clearest case: its only
  id is `mets-results-per-page-select_g_1887`, and its `data-page-size` is what
  survives.
* **One definition per element.** These used to be string literals scattered
  across `scraper.py`, `sidebar.py` and `documents.py`, so a portal change meant
  finding every copy.

Sidebar filter panels are not here — they are generated from `Section` specs in
`filters.py`, which is already a single mapping of the same kind.
"""

from __future__ import annotations

# -- login -------------------------------------------------------------------

LOGIN_BUTTON = "header_btnLogin"        # <button id>, on the public landing page
USERNAME = "j_username"
PASSWORD = "j_password"
LOGIN_SUBMIT = "loginButton"
# Present only once signed in — the post-login menu, used as the session probe.
SIGNED_IN_MARKER = "btnSolicitations"

# -- keyword search ----------------------------------------------------------

# Probed: tag=textarea, name='keywords'. It is a <textarea>, not an <input>, and
# the page also carries a *hidden* `input[name='keywords']` mirroring the value —
# so `input[name='keywords']` selects the wrong node. Anchor on the tag when
# using the name.
SEARCH_INPUT = "solicitationSingleBoxSearch"
SEARCH_INPUT_CSS = "textarea[name='keywords']"
SEARCH_BUTTON = "topSearchButton"       # <button id>

# -- results -----------------------------------------------------------------

# Probed: 4 on a results page — one per result group tab, each with its own
# `.solicitationCount` badge. The Member Agency group id lives in scraper.py.
RESULT_GROUP = ".searchContentGroupContainer"
RESULT_COUNT_BADGE = ".solicitationCount"

# Probed: 25 on a 25-result page. `table.mets-table` is NOT specific enough to
# identify the results table — it matched 3 tables, including an unrelated
# history table whose headers are Action/Item/From/to/Modified By/Date. The row
# class is the reliable handle.
RESULTS_ROW = "tr.mets-table-row"
RESULTS_ROW_SCOPED = "table tbody tr.mets-table-row"

# A results row's cells, probed:
#   [0] class="iconsCell"  — empty
#   [1] class=""           — title + the solicitation link
#   [2] class=""           — CLOSING DATE / LOCATION / agency, as text
ROW_CELL_ICONS = 0
ROW_CELL_TITLE = 1
ROW_CELL_DETAILS = 2

# Ordered candidates for a row's solicitation link, most specific first.
#
# The second entry is the one that matters. Solicitation links come in **two**
# href shapes, both carrying `class="solicitationsTitleLink mets-command-link"`:
#
#   /private/supplier/interception/view-notice/444124954092          (19 of 25)
#   /private/supplier/interception/open-solicitation/9490210669?target=view (6)
#
# so a fallback matching only `view-notice` silently drops the rest — a quarter
# of the page, counted as unparseable rows. `/interception/` is the path segment
# both shapes share and is what the fallback keys on.
ROW_LINK_SELECTORS = (
    "a.solicitationsTitleLink",
    "a[href*='/interception/']",
    "a[href*='view-notice'], a[href*='open-solicitation']",
    "td a[href]",
)

# -- results per page --------------------------------------------------------
#
# Measured against the live footer of the results grid:
#
#   <div class="mets-results-per-page mets-field">
#     <label for="mets-results-per-page-select_g_1887">Results per page:</label>
#     <select id="mets-results-per-page-select_g_1887">
#       <option data-page-url="/private/supplier/solicitations/search?pageNumber=1&pageSize=25…"
#               data-page-size="25" data-page-number="1">25</option>
#       <option … data-page-size="50" …>50</option>
#       <option … data-page-size="100" …>100</option>
#
# The id is a `g_NNN` — regenerated on every render, so it is exactly the kind
# of handle this file exists to refuse. The stable route is the wrapper's class
# plus `data-page-size`, which is also the value the code actually cares about
# (the option's *text* is "100" too, but the attribute is what the portal keys
# its own paging on).
#
# Each option carries a `data-page-url`: a real GET that re-runs the current
# search at that page size. It is the fallback when driving the select does not
# take — see `BidnetScraper.set_page_size`.
RESULTS_PER_PAGE = ".mets-results-per-page"
RESULTS_PER_PAGE_SELECT = ".mets-results-per-page select"


def results_per_page_option(size: int) -> str:
    return f".mets-results-per-page select option[data-page-size='{size}']"


# -- pagination --------------------------------------------------------------
#
# Measured against the live bar, which is `.mets-page-navigation` — **not**
# `.mets-pagination`, and not anything matching `[class*='pagination']`. That
# distinction is not pedantry: `[class*='pagination']` matches the individual
# `mets-pagination-page-icon` elements, so `querySelector` on it returned the
# first *icon* rather than the bar, every descendant lookup inside it missed,
# and the current page silently read as 1 on every page of every run.
#
#   <div class="mets-page-navigation">
#     <span class="mets-icon first disabled mets-pagination-page-icon"></span>
#     <span class="mets-icon previous disabled mets-pagination-page-icon"></span>
#     <span class="mets-page-navigation-number"><span class="selected">1</span></span>
#     <span class="mets-page-navigation-number">
#       <a data-page-number="2" data-page-size="100" href="…pageNumber=2&pageSize=100…">2</a>
#     </span>
#     … 3 … 9 …
#     <a data-page-number="2" rel="next" data-page-size="100" href="…"
#        class="next mets-pagination-page-icon">…</a>
#     <a data-page-number="19" rel="nofollow" data-page-size="100" href="…"
#        class="last mets-pagination-page-icon">…</a>
#   </div>
#
# Two properties of that markup the walk depends on:
#
# * **A disabled control is a `<span>`, an enabled one is an `<a>`.** `first`
#   and `previous` above are spans *and* carry `.disabled`; on the last page
#   `next` becomes one too. So `a.next` is already "next, and usable" — the
#   `:not(.disabled)` below is belt and braces, not the load-bearing part.
# * **`a.last` carries `data-page-number`, which is the total page count.**
#   That is what lets the walk know it has 19 pages to cover instead of
#   inferring the end from a page that failed to advance.
PAGINATION_CONTAINER = ".mets-page-navigation"
PAGINATION_CURRENT = ".mets-page-navigation-number .selected"
PAGINATION_NEXT = "a.next.mets-pagination-page-icon:not(.disabled)"
PAGINATION_LAST = "a.last.mets-pagination-page-icon"
# Every numbered page link in the bar, for reading the highest page offered
# when `a.last` is absent (a result set small enough to list every page).
PAGINATION_NUMBERED = ".mets-page-navigation a[data-page-number]"

# Ordered candidates for the next-page control. The first is the measured one;
# the rest are shape-compatible fallbacks kept because the disabled-state
# classes are the part most likely to be restyled.
NEXT_PAGE_SELECTORS = (
    PAGINATION_NEXT,
    "a[rel='next']:not(.disabled)",
    "a.next:not(.disabled)",
)

# The way back. Same rule as above — an enabled control is an `<a>`, a disabled
# one is a `<span class="… disabled">`, so these match only when there is
# genuinely somewhere to go back to. Note the class is `previous`, not `prev`.
FIRST_PAGE_SELECTORS = (
    "a.first.mets-pagination-page-icon:not(.disabled)",
    "a[rel='first']:not(.disabled)",
    "a.first:not(.disabled)",
)
PAGINATION_BACK = (
    "a.first:not(.disabled), a.previous:not(.disabled), a[rel='prev']:not(.disabled)"
)

# -- documents -----------------------------------------------------------------
#
# Measured against live solicitation pages (they predate the download feature
# being retired and are unchanged by it — the handles describe the portal, not
# what we do with what they find).
#
# The tab body is rendered lazily: until the tab is opened, the attachment
# anchors are simply not in the DOM. Both the desktop tab and its `_mobile`
# duplicate are accepted, because the portal renders both and hides one by CSS —
# which one is visible depends on the viewport a run happens to use.
DOCS_TAB_SELECTORS = (
    "#docs-itemsAbstractTab a",
    "#docs-itemsAbstractTab_mobile a",
    "a[href*='innerTabId=docs-items']",
)
# Every attachment link the portal renders. The id prefix is the reliable one;
# the href match also catches an anchor rendered without that id.
ATTACHMENT_SELECTOR = "a[id^='attachmentDownloadLnk'], a[href*='attachment-download']"
# The tab's own count. Read with `textContent`, never `innerText`: one of the
# desktop/mobile pair is always hidden by CSS and has no *rendered* text, so
# innerText returns "" there — which is exactly how a bid with attachments used
# to be recorded as having none.
DOCS_TAB_IDS = ("docs-itemsAbstractTab", "docs-itemsAbstractTab_mobile")
DOCS_TAB_BADGE = ".tabCount"

# -- detail page -------------------------------------------------------------

DETAIL_FIELD = ".mets-field"            # a labelled field block on a bid page
DETAIL_HEADING = "h1, h2"               # used for the bot-block guard

# -- AJAX / loading ----------------------------------------------------------

# Probed: `<div id="ajaxIndicator" class="mets-ajax-indicator">`, `visible:false`
# while idle. The portal shows it for the duration of a filter or search
# postback, so its *invisibility* is a real "the page has settled" signal rather
# than a guessed sleep.
AJAX_INDICATOR = "#ajaxIndicator, .mets-ajax-indicator"

# -- date panels -------------------------------------------------------------
#
# Keyed by {SECTION} = publishedDate | closingDate. Probed on publishedDate:
#
#   #publishedDateCheckRANGE   input[name='publishedDate.dateType'] value=RANGE
#   #publishedDateRANGE1       input[name='text_publishedDate.localRangeStart']
#                              type=text  readonly=True  disabled=True
#   #publishedDateRANGE1_hidden input[name='publishedDate.localRangeStart']
#                              type=hidden        <-- this is what posts, in ISO
#   #publishedDateSearchButton <button>
#
# The visible field is display-only (mm/dd/yyyy) and its hidden twin carries ISO
# yyyy-mm-dd. See sidebar._set_date_input — writing the display format into the
# twin posts an unparseable date and empties the whole result set.

def date_panel(section: str) -> str:
    return f"#panel_{section}-body"


def date_mode_checkbox(section: str, mode: str) -> str:
    """By id. The attribute-based equivalent is
    `input[name='{section}.dateType'][value='{mode}']`, which the probe confirms
    selects the same node — used as the fallback in `_apply_date`."""
    return f"{section}Check{mode}"


def date_mode_checkbox_css(section: str, mode: str) -> str:
    return f"input[name='{section}.dateType'][value='{mode}']"


def date_field(section: str, control: str) -> str:
    """`control` is RANGE1 | RANGE2 | DAY | WITHIN."""
    return f"{section}{control}"


def date_hidden_field(section: str, control: str) -> str:
    return f"{section}{control}_hidden"


def date_apply(section: str) -> str:
    return f"{section}SearchButton"


def date_clear(section: str) -> str:
    return f"{section}ClearLink"


# The panel validates in place and unhides its own message, e.g.
# "Ending date must be greater or equal to the starting date."
DATE_PANEL_ERROR = ".error"

# jQuery UI's calendar overlay. Probed: present in the DOM as `#ui-datepicker-div`
# even while closed. It renders over the sidebar, so it is hidden before the
# panel's Apply is clicked or the click lands on the calendar.
DATEPICKER_OVERLAY = "#ui-datepicker-div, .ui-datepicker"

# -- status ------------------------------------------------------------------

# Probed: 3 radios sharing name='status' (OPEN / CLOSED / AWARD). Radios, not
# checkboxes — selecting one deselects the others, which is why the driver reads
# the current state before clicking.
STATUS_RADIO_NAME = "status"


def status_radio(value: str) -> str:
    return f"input[name='{STATUS_RADIO_NAME}'][value='{value}']"


# -- keywords panel ----------------------------------------------------------

# Probed: `<textarea id="excludedKeywords" name="excludedKeywords">`, collapsed by
# default (offsetParent null), which is why it is written through the DOM rather
# than typed.
EXCLUDED_KEYWORDS = "excludedKeywords"
EXCLUDED_KEYWORDS_CSS = "textarea[name='excludedKeywords']"
KEYWORDS_APPLY = "keywordsSearchButton"
KEYWORDS_CLEAR = "clearIncludedExcludedKeywords"
