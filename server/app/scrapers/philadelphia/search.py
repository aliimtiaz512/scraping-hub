"""The portal's Advanced Search form, as a set of fields a run can be given.

PHLContracts publishes more than the Open Bids list. Behind the "Advanced" link
in the top bar is a search form that, once Document Type is set to Bid
Solicitations, filters on description, buyer, department, NIGP class, opening
date, status and more. A run given any of these searches instead of walking the
whole open list — everything downstream (detail pages, attachments, the item
text file, the sheet, the database) is unchanged, because what changes is only
which bids arrive at the top of the pipeline.

**Every id here contains a colon**, because the page is JSF: `bidSearchForm:desc`,
`bidSearchForm:openingDateFrom_input`. A colon is a CSS pseudo-selector, so these
are found through Selenium's `By.ID` — which does not parse them as CSS — and
never by a hand-escaped selector. Same rule as the login overlay's fields.

Two of the form's controls populate others over AJAX and so cannot be filled in
document order:

* choosing an **Organization** enables and fills **Department** and reloads
  **Type Code** (the page clicks a hidden button to do it);
* choosing an **NIGP Class** fills **NIGP Class Item**.

`ORDERED_FIELDS` encodes that: the parents are filled first, and the scraper
waits for each dependent control to come alive before filling it.
"""

from __future__ import annotations

from typing import Any

#: The Advanced Search entry point. Held as a URL as well as a link because the
#: link is a script call (`gotoBsoURL(...)`) and a run that cannot find it can
#: still ask for the page it points at.
ADVANCED_SEARCH_PATH = "/bso/view/search/supplier/advancedSearch.xhtml"
#: Where the portal lands once Document Type is Bid Solicitations. Selecting the
#: option is the documented route; this is what that selection navigates to, and
#: the fallback when the dropdown cannot be driven.
BID_SEARCH_PATH = "/bso/view/search/supplier/advancedSearchBid.xhtml"

ADVANCED_LINK_ID = "advancedSearchTopNav"
DOCUMENT_TYPE_SELECT_ID = "advancedSearchForm:documentTypeSelect"
#: The option this feature exists to choose. The page's own script returns early
#: for this value rather than navigating — it re-renders the form over AJAX — so
#: selecting it is a wait, not a page load.
BID_SOLICITATIONS = "BID_SOLICITATIONS"

SEARCH_FORM_ID = "bidSearchForm"
SEARCH_BUTTON_ID = "bidSearchForm:btnBidSearch"
CLEAR_BUTTON_ID = "bidSearchForm:btnBidCancel"
#: Results are written into this container by the same AJAX call that runs the
#: search, so there is no navigation to wait on — only content.
RESULTS_CONTAINER_ID = "advSearchResults"
#: "Match Criteria" — off is All (every filled criterion must match), on is Any.
MATCH_ANY_SWITCH_ID = "bidSearchForm:searchScopeType_input"

#: Free-text criteria: filter key -> the input's id.
TEXT_FIELDS: dict[str, str] = {
    "bid_number": "bidSearchForm:bidNbr",
    "alternate_id": "bidSearchForm:alternateId",
    "description": "bidSearchForm:desc",
    "item_description": "bidSearchForm:itemDesc",
    "opening_date_from": "bidSearchForm:openingDateFrom_input",
    "opening_date_to": "bidSearchForm:openingDateTo_input",
}

#: Dropdown criteria: filter key -> the select's id. The value a user sends is
#: matched against each option's value *and* its visible text, so "Micro
#: Purchase", "MI" and "micro purchase" all reach the same option — the caller
#: does not have to know the portal's internal codes.
SELECT_FIELDS: dict[str, str] = {
    "organization": "bidSearchForm:organization",
    "department": "bidSearchForm:departmentPrefix",
    "buyer": "bidSearchForm:buyer",
    "nigp_class": "bidSearchForm:classId",
    "nigp_class_item": "bidSearchForm:classItemId",
    "type_code": "bidSearchForm:typeCode",
    "status": "bidSearchForm:status",
    "category": "bidSearchForm:categoryCode",
}

#: Which control has to be set before which. Organization fills Department and
#: reloads Type Code; NIGP Class fills NIGP Class Item. Filling a dependent
#: control first would put a value into a `disabled` select holding one empty
#: option — a filter silently dropped.
DEPENDS_ON: dict[str, str] = {
    "department": "organization",
    "nigp_class_item": "nigp_class",
}

#: Fill order: parents, then their dependants, then everything else.
ORDERED_FIELDS: tuple[str, ...] = (
    "organization", "department",
    "nigp_class", "nigp_class_item",
    "buyer", "type_code", "status", "category",
    "bid_number", "alternate_id", "description", "item_description",
    "opening_date_from", "opening_date_to",
)

#: Every key a run accepts, including the one that is not a form field.
FILTER_KEYS: frozenset[str] = frozenset(
    {*TEXT_FIELDS, *SELECT_FIELDS, "match_any"}
)

#: How each key reads in a run summary and in the log.
LABELS: dict[str, str] = {
    "bid_number": "Bid #",
    "alternate_id": "Alternate ID",
    "description": "Description",
    "item_description": "Item description",
    "opening_date_from": "Opening from",
    "opening_date_to": "Opening to",
    "organization": "Organization",
    "department": "Department",
    "buyer": "Buyer",
    "nigp_class": "NIGP class",
    "nigp_class_item": "NIGP class item",
    "type_code": "Type code",
    "status": "Status",
    "category": "Category",
    "match_any": "Match",
}


def clean_filters(raw: Any) -> dict[str, Any]:
    """The filters a run will actually use: known keys, trimmed, non-empty.

    A blank field is not a filter — the portal treats an empty input as "no
    criterion", and carrying it through would only make a run claim to be
    narrower than it is. Unknown keys are dropped rather than passed to the
    page, because the only thing to do with an id that is not on the form is
    fail confusingly later.
    """
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in FILTER_KEYS:
            continue
        if key == "match_any":
            if value:
                cleaned[key] = True
            continue
        text = " ".join(str(value or "").split())
        if text:
            cleaned[key] = text
    return cleaned


def describe(filters: dict[str, Any]) -> str:
    """A one-line summary of a search, for the run row and the console.

    This is what a reader sees months later next to a stored run, so it names
    the criteria in the words the form uses rather than in field ids.
    """
    if not filters:
        return "all open bids"
    parts = []
    for key in ORDERED_FIELDS:
        if key in filters:
            parts.append(f"{LABELS[key]}: {filters[key]}")
    if filters.get("match_any"):
        parts.append("Match: any criterion")
    return " · ".join(parts) or "all open bids"
