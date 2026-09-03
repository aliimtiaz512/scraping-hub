"""The My Network roster, read off the whole page rather than stubbed cards.

This is the reconciliation the client checks the run against: the Active tab
says 24, four of those are Incomplete, and the sweep must therefore queue
exactly 20. It exists because a selector that named only the anchor form of
"Go to Agency" quietly returned 18 — the six agencies whose control is a
hrefless <button> never reached the roster at all, so five Complete agencies
went unscraped and the shortfall looked like six agencies that "did not exist".

The page below is rebuilt from the outer HTML captured off the live account,
with every card's real data-testid, name, status and control type. A small
adapter gives BeautifulSoup nodes the three WebElement methods the scraper
calls, so what runs here is the shipped `read_agencies`, selectors and all —
CSS through soupsieve, and the one XPath (the Status box) emulated.

    server/.venv/bin/python -m pytest server/tests/test_ridemetro_roster.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402
from selenium.common.exceptions import NoSuchElementException  # noqa: E402
from selenium.webdriver.common.by import By  # noqa: E402

from app.scrapers.ridemetro import network  # noqa: E402

# The selector this module is a regression test for.
OLD_GO_TO = "a[aria-label^='Go to ']"
STATUS_XPATH = ".//p[normalize-space()='Status']/ancestor::div[2]"

# (data-testid id, name, date created, status, href) — a None href is the
# <button> form of "Go to Agency", which the SPA navigates itself.
ROSTER = [
    ("1086816-ca", "Agriculture Financial Services Corporation", "2026-08-25", "Incomplete", None),
    ("2974225-us", "City of Hutto, TX", "2026-07-14", "Incomplete", "https://huttotx.bonfirehub.com/registration"),
    ("2974208-us", "Metropolitan Transit Authority of Harris County (METRO)", "2026-07-14", "Incomplete", "https://ridemetro.bonfirehub.com/registration"),
    ("2885859-us", "Boulder County", "2026-06-01", "Incomplete", "https://bouldercounty.bonfirehub.com/registration"),
    ("1086818-ca", "Calgary Catholic School District", "2026-08-25", "Complete", None),
    ("3053421-us", "Rockland County", "2026-08-19", "Complete", "https://rocklandgov.bonfirehub.com"),
    ("3042802-us", "Ashley Procurement Training", "2026-08-13", "Complete", "https://athomason.bonfirehub.com"),
    ("3042801-us", "Allegheny County Department of Human Services", "2026-08-13", "Complete", "https://alleghenycountydhs.bonfirehub.com"),
    ("3042800-us", "Alameda County Transportation Commission", "2026-08-13", "Complete", "https://alamedactc.bonfirehub.com"),
    ("1080664-ca", "Aéroports de Montréal", "2026-08-13", "Complete", None),
    ("1080663-ca", "Aéroport de Québec", "2026-08-13", "Complete", None),
    ("1080662-ca", "Bonfire", "2026-08-13", "Complete", None),
    ("3042794-us", "Ada County Highway District", "2026-08-13", "Complete", "https://achdidaho.bonfirehub.com"),
    ("3042792-us", "Ada County", "2026-08-13", "Complete", "https://adacounty.bonfirehub.com"),
    ("1080661-ca", "Acadia University", "2026-08-13", "Complete", None),
    ("2974230-us", "City of Santa Rosa", "2026-07-14", "Complete", "https://srcity.bonfirehub.com"),
    ("2921235-us", "Union Public Schools", "2026-06-17", "Complete", "https://unionps.bonfirehub.com"),
    ("2885860-us", "AC Transit", "2026-06-01", "Complete", "https://actransit.bonfirehub.com"),
    ("2758628-us", "Tacoma Public Schools", "2026-04-01", "Complete", "https://tacoma.bonfirehub.com"),
    ("2716500-us", "Metra", "2026-03-12", "Complete", "https://metra.bonfirehub.com"),
    ("2692707-us", "Howard County Public School System", "2026-03-02", "Complete", "https://hcpss.bonfirehub.com"),
    ("2648191-us", "City of Dallas", "2026-02-09", "Complete", "https://dallascityhall.bonfirehub.com"),
    ("2576977-us", "Jackson County", "2026-01-05", "Complete", "https://jacksongov.bonfirehub.com"),
    ("2169295-us", "Harris County", "2025-05-14", "Complete", "https://harriscountytx.bonfirehub.com"),
]

# -- the page ----------------------------------------------------------------

CARD = """<li class="MuiBox-root css-15nejzs" data-testid="agency-{aid}-list-item"><div \
class="MuiPaper-root MuiPaper-elevation2 MuiCard-root css-1310bue"><div class="MuiGrid-root MuiGrid-container css-5kl7lv">\
<div class="MuiGrid-root MuiGrid-item MuiGrid-grid-xs-true css-kv8h1e"><span class="MuiCheckbox-root css-1i2y25b">\
<input class="PrivateSwitchBase-input css-1m9pwf3" type="checkbox" data-indeterminate="false" aria-label="Select {name}">\
<svg class="MuiSvgIcon-root" aria-hidden="true" viewBox="0 0 24 24" data-testid="CheckBoxOutlineBlankIcon">\
<path d="M19 5v14H5V5h14"></path></svg></span><img class="MuiBox-root css-1tyoqo6" alt="{name} Logo" \
src="https://example.invalid/logo.png"><p class="MuiTypography-root MuiTypography-body2 css-jws8eq">{name}</p></div>\
<div class="MuiGrid-root MuiGrid-item MuiGrid-grid-xs-true css-kv8h1e"><div class="MuiBox-root css-dvxtzn">\
<div class="MuiBox-root css-1nsfdkm"><span class="material-icons fas fa-calendar-alt css-z4xyog" aria-hidden="true">\
</span><p class="MuiTypography-root MuiTypography-body2 css-1uikq0e">Date Created</p></div>{date}</div></div>\
<div class="MuiGrid-root MuiGrid-item MuiGrid-grid-xs-true css-kv8h1e"><div class="MuiBox-root css-dvxtzn">\
<div class="MuiBox-root css-1nsfdkm"><span class="material-icons fas {icon} css-z4xyog" aria-hidden="true"></span>\
<p class="MuiTypography-root MuiTypography-body2 css-1uikq0e">Status</p></div>{status}</div></div>\
<div class="MuiGrid-root MuiGrid-item css-3rg3ny"><button class="MuiButtonBase-root MuiButton-root css-su9qq8" \
type="button" aria-label="View details for {name}" role="button" aria-expanded="false" aria-controls="{aid}-agency">\
<div class="MuiBox-root css-rrm59m">View Details</div></button>{control}</div></div>\
<div class="MuiBox-root css-0" id="{aid}-agency"></div></div></li>"""

LINK = ('<a class="MuiButtonBase-root MuiButton-contained css-tj8shy" type="button" aria-label="Go to {name}" '
        'role="link" rel="noreferrer noopener" target="_blank" href="{href}">'
        '<div class="MuiBox-root css-rrm59m">Go to Agency</div></a>')
BUTTON = ('<button class="MuiButtonBase-root MuiButton-contained css-tj8shy" type="button" '
          'aria-label="Go to {name}" role="button">'
          '<div class="MuiBox-root css-rrm59m">Go to Agency</div></button>')

TABS = """<div aria-label="Agency Tabs" class="MuiTabs-flexContainer css-k008qs" role="tablist">\
<button class="MuiTab-root Mui-selected css-u5omt" type="button" role="tab" aria-selected="true" \
id="pv-agency-tab-item-1" aria-controls="pv-agency-tab-panel-item-1">Active\
<span class="MuiTypography-subtitle2 css-13f7jf0">({count})</span></button>\
<button class="MuiTab-root css-u5omt" type="button" role="tab" aria-selected="false" id="pv-agency-tab-item-2" \
aria-controls="pv-agency-tab-panel-item-2">Inactive<span class="MuiTypography-subtitle2 css-13f7jf0">(0)</span>\
</button></div>"""


def build_page(roster=ROSTER):
    cards = [
        CARD.format(
            aid=aid, name=name, date=date, status=status,
            icon="fa-check-circle" if status == "Complete" else "fa-times-circle",
            control=LINK.format(name=name, href=href) if href else BUTTON.format(name=name),
        )
        for aid, name, date, status, href in roster
    ]
    return (
        '<html><body><div id="root"><main>' + TABS.format(count=len(roster))
        + '<div role="tabpanel" id="pv-agency-tab-panel-item-1"><ol class="MuiBox-root css-7o79s8">'
        + "".join(cards) + "</ol></div></main></div></body></html>"
    )


# -- the adapter -------------------------------------------------------------


class El:
    """A BeautifulSoup node wearing the WebElement methods the scraper uses."""

    def __init__(self, node):
        self._n = node

    def get_attribute(self, name):
        if name == "textContent":
            return self._n.get_text()
        value = self._n.get(name)
        return " ".join(value) if isinstance(value, list) else value

    @property
    def text(self):
        return self._n.get_text()

    def find_elements(self, by, value):
        assert by == By.CSS_SELECTOR, by
        return [El(n) for n in self._n.select(value)]

    def find_element(self, by, value):
        if by == By.XPATH:
            assert value == STATUS_XPATH, value
            for label in self._n.find_all("p"):
                if label.get_text(strip=True) == "Status":
                    return El(label.parent.parent)
            raise NoSuchElementException(value)
        found = self._n.select_one(value)
        if found is None:
            raise NoSuchElementException(value)
        return El(found)


@pytest.fixture
def page():
    return El(BeautifulSoup(build_page(), "lxml"))


@pytest.fixture
def agencies(page):
    return network.read_agencies(page)


# -- the reconciliation ------------------------------------------------------


def test_every_card_the_active_tab_counts_is_read(page, agencies):
    """24 on the tab, 24 cards in the DOM, 24 in the roster — the three numbers
    the run reconciles, and the check that used to come out 24/24/18."""
    assert network.active_count(page) == 24
    assert len(page.find_elements(*network.SEL["agency_cards"])) == 24
    assert len(agencies) == 24


def test_the_old_anchor_only_selector_is_what_lost_the_six(page):
    """Six cards render "Go to Agency" as a <button>; a locator naming only <a>
    matched 18 of 24, which is exactly the shortfall that was reported."""
    cards = page.find_elements(*network.SEL["agency_cards"])
    assert sum(1 for card in cards if card._n.select_one(OLD_GO_TO)) == 18
    assert sum(1 for card in cards if card._n.select_one(network.SEL["go_to_agency"][1])) == 24


def test_twenty_active_agencies_are_queued_and_four_are_skipped(agencies):
    queued = [a.name for a in agencies if a.is_complete]
    skipped = [a.name for a in agencies if a.is_incomplete]
    assert len(queued) == 20
    assert skipped == [
        "Agriculture Financial Services Corporation",
        "City of Hutto, TX",
        "Metropolitan Transit Authority of Harris County (METRO)",
        "Boulder County",
    ]
    # Every card lands in one bucket or the other: an unreadable status would
    # drop an agency as silently as the old selector did.
    assert len(queued) + len(skipped) == len(agencies)


def test_the_five_active_agencies_that_went_missing_are_back(agencies):
    """The button-rendered Complete agencies — the five whose opportunities the
    sweep never collected."""
    recovered = [a for a in agencies if a.is_complete and not a.url]
    assert [a.name for a in recovered] == [
        "Calgary Catholic School District",
        "Aéroports de Montréal",
        "Aéroport de Québec",
        "Bonfire",
        "Acadia University",
    ]
    # No href is not "no way in": these are opened by clicking the control.
    assert all(a.agency_id.endswith("-ca") for a in recovered)


def test_every_agency_keeps_an_id_a_name_and_the_page_order(agencies):
    assert [a.agency_id for a in agencies] == [row[0] for row in ROSTER]
    assert [a.name for a in agencies] == [row[1] for row in ROSTER]
    assert [a.status for a in agencies] == [row[3] for row in ROSTER]


def test_linked_agencies_keep_their_url(agencies):
    by_id = {a.agency_id: a for a in agencies}
    assert by_id["2169295-us"].url == "https://harriscountytx.bonfirehub.com"
    # An Incomplete agency's link goes to /registration, not to a portal —
    # the other half of why it is not worth visiting.
    assert by_id["2974208-us"].url.endswith("/registration")


def test_view_details_is_not_mistaken_for_go_to_agency(page):
    """Each card carries a second aria-labelled button; the roster must not
    count it as a way into the agency."""
    controls = page.find_elements(*network.SEL["go_to_agency"])
    assert len(controls) == 24
    assert all((c.get_attribute("aria-label") or "").startswith("Go to ") for c in controls)


def test_a_half_rendered_roster_is_not_mistaken_for_a_short_one(page):
    """The count the run checks itself against comes from the tab, not from the
    cards, so a list still filling in is detectable rather than plausible."""
    half = El(BeautifulSoup(build_page(ROSTER[:10]).replace("(10)", "(24)"), "lxml"))
    assert network.active_count(half) == 24
    assert len(network.read_agencies(half)) == 10
