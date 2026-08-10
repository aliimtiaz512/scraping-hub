"""Every DOM handle the BidNet Direct scraper uses, in one place.

**Measured, not assumed.** Each entry below was probed against a live logged-in
results page (`graphic design`, 25 rows) and carries what the probe reported, so
a future change can be checked against evidence instead of memory.

Two rules this file exists to enforce:

* **No `g_NNN` ids.** The portal regenerates them on every render (`g_427`,
  `g_428`, `g_435`…), so anything built on them breaks at the next postback. The
  stable handles are semantic ids, `name` attributes, `data-filter-item-value`,
  and href *paths*.
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

# Probed: present as `a.next`. Kept as a chain because the disabled-state classes
# vary between the first, middle and last page.
NEXT_PAGE_SELECTORS = (
    "a.next.mets-pagination-page-icon:not(.disabled)",
    "a[rel='next']:not(.disabled)",
    "a.next:not(.disabled)",
)

# The way back. **Derived from `a.next`'s shape, not probed** — the pagination
# widget renders its icons as a set, so `a.first`/`a.prev` are the expected
# siblings of the `a.next` above, but that has not been confirmed against a live
# page the way everything else in this file has.
#
# Nothing depends on them being right: they are used only by
# `_ensure_first_result_page`, which reads the pagination *before* deciding
# anything and falls back to reloading the search when it cannot get back to
# page 1. A selector that matches nothing costs a reload, not a lost page.
FIRST_PAGE_SELECTORS = (
    "a.first.mets-pagination-page-icon:not(.disabled)",
    "a[rel='first']:not(.disabled)",
    "a.first:not(.disabled)",
)

# The pagination bar itself, and the marks a widget uses for "the page you are
# on". Same caveat as above: read defensively, never assumed.
PAGINATION_CONTAINER = ".mets-pagination, [class*='pagination']"
PAGINATION_CURRENT = ".current, .active, .selected, [aria-current]"
PAGINATION_BACK = (
    "a.first:not(.disabled), a.prev:not(.disabled), a[rel='prev']:not(.disabled)"
)

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
