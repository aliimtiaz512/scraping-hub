"""Drives the BidNet Direct search sidebar from a Selenium session.

Two jobs, both generic over the panel specs in `filters.py`:

* `SidebarDriver.apply()` — turn a `SidebarFilterRequest` into the portal's own
  filter state, applied to the current results page.
* `SidebarDriver.harvest()` — read every option a panel offers (including the
  ones hidden behind "View All") so the frontend can show the complete list.

Why the selection is written into the hidden fields rather than clicked
---------------------------------------------------------------------
Every list panel is `class="auto-search filterPanel"`: ticking one checkbox
fires a full search postback. Selecting twelve NIGP codes by clicking would mean
twelve round-trips through a slow portal, and any option outside the panel's
inline top-12 slice is not clickable at all without first opening its "View All"
lightbox. But each panel also carries the *authoritative* control the page
itself submits:

    <input id="regionId" name="regionId" class="filterHiddenField" type="hidden"
           value="49,211,151">

so writing the comma-joined selection into those fields and submitting the search
form once applies every panel in a single round-trip, for any value — inline or
not. `apply()` does that, then re-reads the fields after the reload to confirm
the portal kept them; if it did not, it falls back to clicking the inline
checkboxes one panel at a time (`_apply_by_click`), which is slower and can only
reach inline options, but uses nothing but the page's own handlers.

Status is a radio (`input[name=status]`) and the two date panels are
checkbox+control+Apply groups, so both are always driven by clicking — they are
single interactions, so there is nothing to batch.

Applied once, then watched
--------------------------
The panels belong to the *search form*, not to a keyword: once applied they stay
in force for every subsequent search of the same session, which is why the
scraper applies them a single time before its keyword loop and re-types keywords
into the box rather than reloading the page. `state_intact()` is the other half
of that: a read-only re-read of everything `apply()` set — status radio, hidden
section fields, both date panels — so the loop can confirm before each keyword
that the filters are still there, and only pay for a re-`apply()` when they are
not. It clicks nothing and posts nothing, so checking is cheap enough to do on
every keyword.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Callable

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.scrapers.bidnet import selectors
from app.scrapers.bidnet.filters import (
    DATE_SECTIONS,
    SECTIONS,
    STATUS_RADIO_NAME,
    DateFilter,
    Section,
    SidebarFilterRequest,
)

logger = logging.getLogger(__name__)

# The portal reloads the results list on every filter postback; these are how
# long we give it and how long we let the DOM settle before reading it back.
POSTBACK_TIMEOUT = 60
SETTLE_SECONDS = 3

# Results-table row — present once a postback has re-rendered the list. A search
# that legitimately matches nothing has no rows, so waits on it are best-effort.
RESULTS_ROW = selectors.RESULTS_ROW_SCOPED

# The sidebar's Keywords panel. Unlike the list panels this is free text, so
# there is no hidden field to write and no catalog to pick from: the terms are
# typed into the textarea and the panel's own Apply button posts them.
#
#   <textarea id="excludedKeywords" name="excludedKeywords" rows="3" cols="25">
#   <button id="keywordsSearchButton"  … >Apply</button>
#   <button id="clearIncludedExcludedKeywords" … >Clear</button>
EXCLUDED_KEYWORDS_ID = selectors.EXCLUDED_KEYWORDS
KEYWORDS_APPLY_ID = selectors.KEYWORDS_APPLY
KEYWORDS_CLEAR_ID = selectors.KEYWORDS_CLEAR

# Reads a panel's <li data-filter-item-value> entries as {value, label}. Labels
# come from the `title` attribute on <element> (the untruncated text) and fall
# back to the checkbox's own .inputText.
_JS_HARVEST = """
const body = document.getElementById(arguments[0]);
if (!body) return [];
return Array.from(body.querySelectorAll('li[data-filter-item-value]')).map((li) => {
  const el = li.querySelector('element');
  const text = li.querySelector('.inputText');
  return {
    value: li.getAttribute('data-filter-item-value'),
    label: ((el && el.getAttribute('title')) || (text && text.textContent) || '').trim(),
  };
}).filter((o) => o.value);
"""

# Same shape, but scoped to the "View All" lightbox, whose markup reuses
# data-filter-item-value without the surrounding <li>/<element> wrapper being
# guaranteed — so it reads the checkbox spans directly.
_JS_HARVEST_DIALOG = """
const root = document.querySelector(arguments[0]);
if (!root) return [];
const seen = new Set();
const out = [];
root.querySelectorAll('[data-filter-item-value]').forEach((node) => {
  const value = node.getAttribute('data-filter-item-value');
  if (!value || seen.has(value)) return;
  seen.add(value);
  const li = node.closest('li');
  const el = li && li.querySelector('element');
  const text = node.querySelector('.inputText') || (li && li.querySelector('.inputText'));
  out.push({
    value: value,
    label: ((el && el.getAttribute('title')) || (text && text.textContent) || '').trim(),
  });
});
return out;
"""

# Writes the selections into the panels' hidden fields and submits the search
# form once. Returns the ids it could not find so the caller can fall back.
#   arguments[0]: [{id, value}, ...]  — hidden field id -> comma-joined selection
_JS_SET_AND_SUBMIT = """
const updates = arguments[0];
const missing = [];
let form = null;
updates.forEach((u) => {
  const field = document.getElementById(u.id);
  if (!field) { missing.push(u.id); return; }
  field.value = u.value;
  form = form || field.form;
});
if (missing.length) return {missing: missing, submitted: false};
if (!form) return {missing: ['<no form>'], submitted: false};
// The page tags each postback with which filter action triggered it. A bulk
// write is not one action, so the criterion is cleared and the submit is
// treated as a plain "apply the current criteria" request.
const criterion = form.querySelector("input[name='actionFilterCriterion']");
const value = form.querySelector("input[name='actionFilterValue']");
const selected = form.querySelector("input[name='actionFilterSelected']");
if (criterion) criterion.value = '';
if (value) value.value = '';
if (selected) selected.value = 'true';
// requestSubmit() fires the form's own submit event, so BidNet's jQuery handlers
// (which normalise the criteria before posting) still run. submit() would skip
// them entirely — it is only the fallback for a browser without requestSubmit.
if (typeof form.requestSubmit === 'function') { form.requestSubmit(); }
else { form.submit(); }
return {missing: [], submitted: true};
"""

# Reads the hidden fields back after a postback, to confirm the portal kept them.
_JS_READ_FIELDS = """
const out = {};
arguments[0].forEach((id) => {
  const field = document.getElementById(id);
  out[id] = field ? field.value : null;
});
return out;
"""

# True once a filter postback has resolved either way: rows on the page, or
# every result group reporting zero. Without the second arm an empty result is
# unreachable — the wait can only end by timing out.
#   arguments[0]: the results-row selector
_JS_POSTBACK_SETTLED = """
if (document.querySelector(arguments[0])) return true;
const counts = Array.from(document.querySelectorAll('.solicitationCount'));
if (!counts.length) return false;                 // no badges yet — still loading
return counts.every((n) => ((n.textContent || '').replace(/[^0-9]/g, '') || '0') === '0');
"""

# Reads a date panel's state back after its postback. `null` for a control means
# the element is not on the page at all, which is the difference between "the
# portal dropped what we set" and "we were writing into nothing all along" —
# the two used to be indistinguishable, because every write was a silent no-op
# on a missing element and the panel still reported itself applied.
#   arguments[0]: section prefix ("publishedDate"), arguments[1]: mode ("RANGE")
_JS_READ_DATE = """
const section = arguments[0];
// The visible field and its `_hidden` twin both matter, and the twin is the one
// the form posts. Reading only the visible one cannot tell a filter that posted
// correctly (twin set, visible field re-rendered blank) from one that posted
// empty — so either carrying the value counts as kept.
const val = (id) => {
  const shown = document.getElementById(id);
  const twin = document.getElementById(id + '_hidden');
  if (!shown && !twin) return null;
  return ((shown && shown.value) || (twin && twin.value) || '');
};
const chk = (id) => { const el = document.getElementById(id); return el ? !!el.checked : null; };
return {
  mode_checked: chk(section + 'Check' + arguments[1]),
  range_start: val(section + 'RANGE1'),
  range_end: val(section + 'RANGE2'),
  day: val(section + 'DAY'),
  within: val(section + 'WITHIN'),
};
"""


class SidebarFilterError(Exception):
    """The sidebar could not be put into the requested state."""


def _iso(text: str) -> str:
    """mm/dd/yyyy -> yyyy-mm-dd, the format the posted hidden field carries.

    Empty for anything that is not a full US date, so a half-typed value is
    never turned into a plausible-looking wrong one.
    """
    match = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", text or "")
    if not match:
        return ""
    month, day, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _same_date(left: str, right: str) -> bool:
    """True if two date strings mean the same day in either of the panel's two
    formats. The visible field is mm/dd/yyyy and its hidden twin is yyyy-mm-dd,
    so a read-back has to compare on meaning rather than on text."""
    left, right = (left or "").strip(), (right or "").strip()
    if not left or not right:
        return left == right
    return left == right or _iso(left) == right or left == _iso(right) or (
        _iso(left) and _iso(left) == _iso(right)
    )


class SidebarDriver:
    """Applies and reads the search sidebar. `note` receives human-readable
    progress/problem lines (the scraper wires it to the run's error list)."""

    def __init__(
        self,
        driver,
        note: Callable[[str], None] | None = None,
        debug: Callable[..., None] | None = None,
    ):
        self.driver = driver
        self.note = note or (lambda message: None)
        # Live-debug announcer. The scraper owns the headed-mode flag and passes
        # its `live_debug`; default is a no-op so nothing here depends on it.
        self.debug = debug or (lambda *_a, **_k: None)

    # -- public ------------------------------------------------------------

    def apply(self, request: SidebarFilterRequest) -> dict[str, object]:
        """Put the sidebar into the requested state on the current results page.

        Returns a report of what was applied: ``{status, sections: {name: n},
        dates: [...], strategy: 'bulk'|'click'}``. Raises nothing for a panel
        that simply isn't on the page — that is reported through `note` and the
        run continues with the filters that did apply, since a partial filter
        still yields usable (if broader) results.
        """
        report: dict[str, object] = {"status": request.status, "sections": {}, "dates": []}

        self._apply_status(request.status)

        wanted = [
            (section, chosen)
            for section in SECTIONS
            if (chosen := request.selection_for(section)) is not None
        ]
        if wanted:
            strategy = "bulk" if self._apply_bulk(wanted) else "click"
            if strategy == "click":
                self.note(
                    "BidNet did not keep the bulk filter selection; fell back to "
                    "clicking the sidebar checkboxes (inline options only)."
                )
                self._apply_by_click(wanted)
            report["strategy"] = strategy
            report["sections"] = {section.name: len(chosen) for section, chosen in wanted}

        applied_dates = []
        for name, value in request.dates():
            if self._apply_date(name, value):
                applied_dates.append(f"{name}:{value.type}")
        report["dates"] = applied_dates

        # Last, so the exclusions are applied to whatever the other panels
        # narrowed the search to. No terms means the panel is never touched and
        # the search stays exactly as it was.
        expression = request.excluded_keywords_expression()
        if expression and self._apply_excluded_keywords(expression):
            report["excluded_keywords"] = request.excluded_keyword_list()
            report["excluded_expression"] = expression
        return report

    def state_intact(self, request: SidebarFilterRequest) -> tuple[bool, list[str]]:
        """Is the sidebar still holding everything `request` asked for?

        Read-only: no clicks, no writes, no postback — just the same three reads
        `apply()` verifies itself with (the status radio, the panels' hidden
        fields, the date panels' controls), run against whatever page is on
        screen now.

        This is what makes applying the filters once and keeping them for the
        whole run safe. The filters live in the search form, so a new keyword
        search carries them along — but "should carry" is not "did carry", and a
        session that quietly lost its Published Date window would export
        out-of-window bids under a run record claiming the window was applied.
        Checking costs one round-trip per keyword instead of the four postbacks a
        blind re-apply costs.

        Returns ``(intact, drifted)`` — `drifted` naming each panel that no
        longer matches, in the same words the notes use, and empty when intact.
        """
        drifted: list[str] = []

        status = self._current_status()
        if status is not None and status != request.status:
            drifted.append(f"status is {status!r}, not the requested {request.status!r}")

        wanted = [
            (section, chosen)
            for section in SECTIONS
            if (chosen := request.selection_for(section)) is not None
        ]
        if wanted:
            drifted.extend(self._sections_drift(wanted))

        for name, value in request.dates():
            differences = self._date_differences(DATE_SECTIONS[name], value)
            if differences is None:
                drifted.append(f"{name}: the date panel could not be read off the page")
            elif differences:
                drifted.append(f"{name}: {'; '.join(differences)}")

        return not drifted, drifted

    def _current_status(self) -> str | None:
        """The status radio the page currently has ticked, or None if unreadable."""
        try:
            return self.driver.execute_script(
                f"const el = document.querySelector(\"input[name='{STATUS_RADIO_NAME}']:checked\");"
                "return el ? el.value : null;"
            )
        except WebDriverException:
            return None

    def harvest(self) -> dict[str, list[dict[str, str]]]:
        """Every option each list panel offers, inline plus behind "View All".

        Used by the option-discovery run. A panel that cannot be opened yields
        whatever was inline rather than failing the whole pass.
        """
        harvested: dict[str, list[dict[str, str]]] = {}
        for section in SECTIONS:
            inline = self._harvest_inline(section)
            full = self._harvest_view_all(section)
            # "View All" is the complete list when it opens; the inline slice is
            # the fallback (and fills any label the dialog left blank).
            options = full or inline
            labels = {o["value"]: o["label"] for o in inline if o["label"]}
            for option in options:
                if not option["label"]:
                    option["label"] = labels.get(option["value"], option["value"])
            harvested[section.name] = options
            logger.info("[bidnet] harvested %s option(s) for %s", len(options), section.name)
        return harvested

    # -- status ------------------------------------------------------------

    def _apply_status(self, status: str) -> None:
        """Tick the Status radio. It sits in an `auto-search` wrapper, so the
        click is itself the search postback."""
        selector = f"input[name='{STATUS_RADIO_NAME}'][value='{status}']"
        try:
            radio = self.driver.find_element(By.CSS_SELECTOR, selector)
        except WebDriverException:
            self.note(f"Status filter '{status}' was not on the page; left as the portal default.")
            return
        if radio.get_attribute("checked") or radio.is_selected():
            return  # already the active status — clicking would be a pointless postback
        # The real <input> is visually replaced by a styled <span class="radio">,
        # so a native click can land on the overlay; a JS click always reaches the
        # input and fires the page's own change handler.
        anchor = self._results_anchor()
        self.driver.execute_script("arguments[0].click();", radio)
        self._await_replaced(anchor)

    # -- list sections -----------------------------------------------------

    def _apply_bulk(self, wanted: list[tuple[Section, list[str]]]) -> bool:
        """Write every selection into the hidden fields and submit once.

        Returns True when the portal came back with those selections intact.
        """
        updates = [
            {"id": section.hidden_field_id, "value": ",".join(chosen) if chosen else section.empty_value}
            for section, chosen in wanted
        ]
        anchor = self._results_anchor()
        try:
            result = self.driver.execute_script(_JS_SET_AND_SUBMIT, updates)
        except WebDriverException as exc:
            logger.info("[bidnet] bulk filter submit failed: %s", exc.__class__.__name__)
            return False
        if not result or not result.get("submitted"):
            missing = ", ".join(result.get("missing", [])) if result else "?"
            logger.info("[bidnet] bulk filter submit skipped (missing fields: %s)", missing)
            return False

        self._await_replaced(anchor)
        return self._verify(wanted)

    def _verify(self, wanted: list[tuple[Section, list[str]]]) -> bool:
        """Re-read the hidden fields; every requested selection must have survived."""
        return not self._sections_drift(wanted)

    def _sections_drift(self, wanted: list[tuple[Section, list[str]]]) -> list[str]:
        """Which of `wanted` the page is no longer holding, described.

        The comparison `_verify` has always made, split out so the same read
        serves both the post-apply check and `state_intact`'s per-keyword one —
        two implementations of "did this stick?" would eventually disagree, and
        the one that drifted would be the one deciding whether an export is
        trustworthy.
        """
        try:
            values = self.driver.execute_script(
                _JS_READ_FIELDS, [section.hidden_field_id for section, _ in wanted]
            )
        except WebDriverException:
            return ["the sidebar's hidden fields could not be read"]
        drifted: list[str] = []
        for section, chosen in wanted:
            raw = values.get(section.hidden_field_id)
            if raw is None:
                logger.info("[bidnet] %s hidden field gone after submit", section.name)
                drifted.append(f"{section.label}: the panel's hidden field is gone")
                continue
            got = {v for v in raw.split(",") if v and v != section.empty_value}
            if got != set(chosen):
                logger.info(
                    "[bidnet] %s did not stick: wanted %s, page has %s",
                    section.name, len(chosen), len(got),
                )
                drifted.append(
                    f"{section.label}: asked for {len(chosen)} option(s), "
                    f"the page holds {len(got)}"
                )
        return drifted

    def _apply_by_click(self, wanted: list[tuple[Section, list[str]]]) -> None:
        """Fallback: click each panel's inline checkboxes into the requested state.

        One postback per changed checkbox — the panels are `auto-search`. Values
        that are not rendered inline cannot be reached this way and are reported.
        """
        for section, chosen in wanted:
            target = set(chosen)
            inline = {o["value"] for o in self._harvest_inline(section)}
            unreachable = target - inline
            if unreachable:
                self.note(
                    f"{section.label}: {len(unreachable)} selected option(s) are not shown "
                    "in the sidebar's inline list and could not be applied by clicking."
                )
            for value in sorted(inline):
                should_be_checked = value in target
                self._set_checkbox(section, value, should_be_checked)

    def _set_checkbox(self, section: Section, value: str, checked: bool) -> None:
        """Bring one inline checkbox to `checked`, if it isn't already."""
        selector = (
            f"#{section.panel_body_id} li[data-filter-item-value='{value}'] "
            "input[type='checkbox']"
        )
        try:
            box = self.driver.find_element(By.CSS_SELECTOR, selector)
        except WebDriverException:
            return
        anchor = self._results_anchor()
        try:
            if box.is_selected() == checked:
                return
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", box)
            self.driver.execute_script("arguments[0].click();", box)
        except WebDriverException as exc:
            logger.info("[bidnet] could not toggle %s=%s: %s", section.name, value, exc.__class__.__name__)
            return
        self._await_replaced(anchor)

    # -- date panels -------------------------------------------------------

    def _apply_date(self, name: str, value: DateFilter) -> bool:
        """Set one date panel and press its Apply button.

        The panel is a checkbox per mode plus that mode's control; the text
        inputs start `disabled` and readonly (they are datepicker-driven), so the
        value is written through JS into both the visible field and its
        `_hidden` twin — the hidden one is what the form actually posts.
        """
        section = DATE_SECTIONS[name]
        try:
            checkbox = self.driver.find_element(By.ID, f"{section}Check{value.type}")
        except WebDriverException:
            self.note(f"{name}: the portal has no '{value.type}' option; date filter skipped.")
            return False

        try:
            # Ticking the mode checkbox is itself a search postback, and the
            # dates must be written into the page that comes back from it. Doing
            # both in one breath is what produced an empty range: measured on a
            # live run, the tick, both writes and the Apply click all completed
            # inside ~170ms, so the values went into a DOM the in-flight postback
            # then replaced — and what reached BidNet was dateType=RANGE with no
            # dates, which it answers with zero results for every keyword.
            if not checkbox.is_selected():
                self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", checkbox)
                anchor = self._results_anchor()
                self.driver.execute_script("arguments[0].click();", checkbox)
                self._await_replaced(anchor)

            self.debug("Interacting with date input field (%s %s)...", name, value.describe())
            missing = self._write_date_controls(section, value)
            if not missing and self._verify_date(name, section, value, quiet=True):
                return True

            # One retry, because the first attempt's writes can still be lost to
            # a postback that had not finished when they were made. Re-written
            # into the settled page, they stick.
            if not missing:
                logger.info(
                    "[bidnet] %s date filter did not stick on the first attempt — "
                    "rewriting into the settled page", name,
                )
                missing = self._write_date_controls(section, value)
        except WebDriverException as exc:
            self.note(f"{name}: could not apply the date filter ({exc.__class__.__name__}).")
            return False

        # A date panel narrows harder than any other filter — a week-long window
        # can take a search to zero on its own — so "applied" has to mean the
        # portal is holding these dates, not merely that nothing raised. Every
        # write is a no-op on a missing element, which without this check
        # produced a panel that reported success while filtering on an empty
        # range: zero results for every keyword, and nothing in the log to say why.
        if missing:
            self.note(
                f"{name}: the portal did not offer {', '.join(missing)}, so the date "
                f"filter was only partly set. Results may be filtered on an "
                f"incomplete range — treat this run's counts as unreliable."
            )
            return False
        return self._verify_date(name, section, value)

    def _write_date_controls(self, section: str, value: DateFilter) -> list[str]:
        """Write this mode's controls and press Apply, waiting out the postback.

        Returns the ids that were not on the page — empty means every control
        this mode needs was written and submitted.
        """
        missing: list[str] = []
        if value.type == "WITHIN":
            if not self._set_select(f"{section}WITHIN", value.within):
                missing.append(f"{section}WITHIN")
        elif value.type == "DAY":
            if not self._set_date_input(f"{section}DAY", value.day or "", section):
                missing.append(f"{section}DAY")
        elif value.type == "RANGE":
            if not self._set_date_input(f"{section}RANGE1", value.range_start or "", section):
                missing.append(f"{section}RANGE1")
            if not self._set_date_input(f"{section}RANGE2", value.range_end or "", section):
                missing.append(f"{section}RANGE2")

        # The panel validates before it will search, and says so in its own
        # markup — `<div class="message151 error hidden">Ending date must be
        # greater or equal to the starting date.</div>`, unhidden when it fires.
        # Reading it turns a filter the portal silently refused into a stated
        # reason.
        complaint = self._panel_complaint(section)
        if complaint:
            self.note(f"{section}: BidNet rejected the dates — “{complaint}”")
            logger.warning("[bidnet] %s validation error: %s", section, complaint)
            missing.append(f"{section}:validation")
            return missing

        if not self._press_apply(section):
            missing.append(f"{section}:apply-did-not-post")
        return missing

    # How many times to press a date panel's Apply before giving up on it.
    APPLY_ATTEMPTS = 3

    def _press_apply(self, section: str) -> bool:
        """Press the panel's Apply and confirm the portal actually acted on it.

        Pressing is not the same as applying, and conflating the two is what let
        out-of-window bids into an export that reported its filter verified.
        The Apply button is a jQuery `commandButton` constructed with
        `enabled: "false"`:

            <button id="publishedDateSearchButton" aria-disabled="true"
                    class="… mets-command-button disabled">Apply</button>
            <script>$("#publishedDateSearchButton").commandButton({enabled:"false", …})</script>

        so the widget latched a disabled state at construction and can ignore a
        click even after the CSS class and `aria-disabled` are cleared. When it
        does, the date fields still hold the values *we* wrote into the DOM — so
        reading them back proves nothing, and the panel used to call that
        success while the results stayed unfiltered.

        The only proof is the results being replaced. Pressed up to
        APPLY_ATTEMPTS times; the second press generally lands because
        `updateDateStatus` has by then enabled the button through the page's own
        path.
        """
        for attempt in range(1, self.APPLY_ATTEMPTS + 1):
            anchor = self._results_anchor()
            if not self._click_apply(f"{section}SearchButton"):
                self.note(
                    f"{section}: the panel has no Apply button, so the dates were "
                    f"set but never submitted — results are unfiltered by date."
                )
                return False
            if self._await_replaced(anchor):
                if attempt > 1:
                    logger.info(
                        "[bidnet] %s Apply posted on attempt %d", section, attempt
                    )
                return True
            logger.info(
                "[bidnet] %s Apply did not post (attempt %d/%d) — the results were "
                "not replaced; pressing again",
                section, attempt, self.APPLY_ATTEMPTS,
            )

        self.note(
            f"{section}: BidNet did not act on the date panel's Apply button after "
            f"{self.APPLY_ATTEMPTS} attempts — the results were never re-filtered, "
            f"so this keyword's bids are NOT restricted to the requested dates."
        )
        return False

    def _panel_complaint(self, section: str) -> str:
        """The panel's own visible validation message, or "" when it is happy."""
        try:
            return (self.driver.execute_script(
                """
                const panel = document.getElementById('panel_' + arguments[0] + '-body');
                if (!panel) return '';
                for (const node of panel.querySelectorAll('.error')) {
                  if (node.classList.contains('hidden')) continue;
                  if (node.offsetParent === null) continue;   // hidden another way
                  const text = (node.textContent || '').trim();
                  if (text) return text;
                }
                return '';
                """,
                section,
            ) or "").strip()
        except WebDriverException:
            return ""

    def _verify_date(
        self, name: str, section: str, value: DateFilter, quiet: bool = False
    ) -> bool:
        """Read a date panel back after its postback and confirm it holds what
        was asked for. Reports a disagreement rather than failing the run: a
        filter the portal reshaped still produces usable results, but the run
        must not claim it applied what it did not.

        `quiet` is for the probe before the retry — a first attempt that did not
        stick is recovered, not something to report.
        """
        differences = self._date_differences(section, value)
        if differences is None:
            return False

        if differences:
            if quiet:
                return False
            blank = all("page has ''" in d for d in differences)
            consequence = (
                " BidNet is filtering on an empty date range, which it answers with "
                "zero results for every keyword — the counts in this run are not a "
                "real reflection of what the portal holds."
                if blank else
                " The results are filtered on the portal's values above, not the "
                "ones requested."
            )
            self.note(
                f"{name}: BidNet did not keep the date filter as set — "
                f"{'; '.join(differences)}.{consequence}"
            )
            logger.warning("[bidnet] %s date filter drifted: %s", name, "; ".join(differences))
            return False

        logger.info(
            "[bidnet] %s date filter verified on the page: %s", name, value.describe()
        )
        return True

    def _date_differences(self, section: str, value: DateFilter) -> list[str] | None:
        """How the page's date panel differs from `value`, read only.

        `[]` means the panel is holding exactly what was asked for; `None` means
        it could not be read at all, which is a different answer from "it holds
        something else" and must not be collapsed into one.
        """
        try:
            state = self.driver.execute_script(_JS_READ_DATE, section, value.type)
        except WebDriverException:
            return None
        if not state:
            return None

        wanted = {"RANGE": [("range_start", value.range_start), ("range_end", value.range_end)],
                  "DAY": [("day", value.day)],
                  "WITHIN": [("within", value.within)]}.get(value.type, [])
        # Compared on meaning, not text: the visible field holds 08/04/2026 and
        # its posted twin holds 2026-08-04, so a literal comparison would report
        # a correctly applied window as drifted every single time.
        differences = [
            f"{field}: asked {expected!r}, page has {state.get(field)!r}"
            for field, expected in wanted
            if expected and not _same_date(expected, state.get(field) or "")
        ]
        if state.get("mode_checked") is False:
            differences.append(f"the '{value.type}' checkbox is not ticked")
        return differences

    def _set_select(self, element_id: str, value: str) -> bool:
        """Set a <select> and fire `change` — the panel's own handler listens for
        it to re-enable the Apply button. False when the element is not there."""
        return bool(self.driver.execute_script(
            """
            const select = document.getElementById(arguments[0]);
            if (!select) return false;
            select.disabled = false;
            select.value = arguments[1];
            select.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
            """,
            element_id,
            value,
        ))

    def _set_date_input(self, element_id: str, text: str, section: str = "") -> bool:
        """Put a date into a datepicker field, its hidden twin, and the page's model.

        The two fields hold *different formats*, which is the whole reason this
        is not a one-line assignment. From the panel's own markup:

            <input id="publishedDateRANGE1"        value="08/04/2026">
            <input id="publishedDateRANGE1_hidden" value="2026-08-04"
                   name="publishedDate.localRangeStart">

        The visible field is display-only (`readonly`, datepicker-driven); the
        `_hidden` twin is what the form actually posts, and it is **ISO
        yyyy-mm-dd**. Writing the US format into both — which is what this did —
        posts a start date the server cannot parse, so the range collapses to
        empty and every keyword comes back with zero results while the panel
        still looks correctly filled in.

        So the value goes in through the page's own machinery wherever possible:
        `$(input).datepicker('setDate', …)` formats the visible field per the
        widget's `dateFormat: "mm/dd/yy"` and runs its `onSelect`
        (`calendarTagOnSelect`), which is what normally syncs the twin. The twin
        is then set explicitly as a backstop, and `updateDateStatus(section)` —
        the function the field's own `onchange` calls — is invoked to re-run the
        panel's validation, because that is what enables the Apply button.

        `send_keys` is not an option: the field is `readonly` and focusing it
        opens a calendar overlay that swallows keystrokes.

        Returns False when the field is not on the page.
        """
        return bool(self.driver.execute_script(
            """
            const id = arguments[0], text = arguments[1], iso = arguments[2];
            const section = arguments[3];
            const input = document.getElementById(id);
            if (!input) return false;
            const hidden = document.getElementById(id + '_hidden');

            // The DAY field ships `disabled` and every field is `readonly` until
            // its checkbox is ticked; clear both so the assignment lands.
            input.disabled = false;
            input.readOnly = false;

            // Blank first, so no previously parsed date survives underneath —
            // this panel is re-driven once per keyword, 22 times a run.
            input.value = '';
            if (hidden) hidden.value = '';

            // Preferred path: the page's own widget, which formats the visible
            // field and fires the hooks that keep the twin in step.
            let viaWidget = false;
            if (window.jQuery) {
              try {
                const $input = jQuery(input);
                if ($input.data('datepicker')) {
                  $input.datepicker('setDate', text);
                  viaWidget = true;
                }
              } catch (e) { viaWidget = false; }
            }
            if (!viaWidget) input.value = text;

            // Backstop: the twin must carry ISO whatever route got us here.
            if (hidden && iso) hidden.value = iso;

            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.dispatchEvent(new Event('change', {bubbles: true}));
            input.dispatchEvent(new Event('blur', {bubbles: true}));

            // The panel's own validator. The inline onchange calls it, and it is
            // what lifts the Apply button out of its initial disabled state
            // (the button is a commandButton widget built with enabled:"false",
            // so clearing the CSS class alone does not make it act).
            if (section && typeof window.updateDateStatus === 'function') {
              try { window.updateDateStatus(section); } catch (e) {}
            }

            // Dismiss the calendar if setting the value opened it: it renders
            // over the sidebar and would eat the Apply click below.
            if (window.jQuery) {
              try { jQuery(input).datepicker('hide'); } catch (e) {}
            }
            document.querySelectorAll('#ui-datepicker-div, .ui-datepicker').forEach((cal) => {
              cal.style.display = 'none';
            });
            input.blur();
            return true;
            """,
            element_id,
            text,
            _iso(text),
            section,
        ))

    def _apply_excluded_keywords(self, expression: str) -> bool:
        """Write the exclusion expression into the Keywords panel and apply it.

        `expression` is already in the portal's own syntax — terms joined with
        `OR`, phrases quoted — because the box is a boolean query field rather
        than a list; see `SidebarFilterRequest.excluded_keywords_expression`.

        Returns False, with a note, when the panel is not on the page — a
        partial filter still yields usable (if broader) results, same as every
        other panel here, and the run carries on.

        The value is set through the DOM followed by `input`/`change` events,
        not typed. The panel is **collapsed by default** — on the live portal the
        textarea reports `offsetParent: null` — so `send_keys` raises
        "element not interactable"; assigning the value reaches it regardless of
        whether the accordion happens to be open.
        """
        text = expression
        applied = self.driver.execute_script(
            """
            const box = document.getElementById(arguments[0]);
            if (!box) return false;
            box.value = arguments[1];
            box.dispatchEvent(new Event('input', {bubbles: true}));
            box.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
            """,
            EXCLUDED_KEYWORDS_ID,
            text,
        )
        if not applied:
            self.note(
                "BidNet's sidebar had no Excluded Keywords box on this page — "
                "the search ran without excluding those terms."
            )
            return False

        # Hold a node from the *current* results before applying, so the wait can
        # key off it being replaced. Waiting for rows to be "present" is not
        # enough here: the old rows stay in the DOM until the postback swaps
        # them, so that wait returns instantly and the caller reads the
        # unfiltered page — measured on the live portal, where re-reading the
        # count straight after an apply returned the previous filter's number.
        anchor = self._results_anchor()
        if not self._click_apply(KEYWORDS_APPLY_ID):
            self.note(
                "BidNet's Keywords panel had no Apply button, so the excluded terms "
                "were typed but never submitted — the results are unfiltered by them."
            )
            return False
        self._await_refresh(anchor)
        logger.info("applied excluded keywords: %s", expression)
        return True

    def _results_anchor(self):
        """A node from the current results, to watch for replacement. None if the
        page has no rows yet (an empty search), where there is nothing to wait on."""
        try:
            return self.driver.find_element(By.CSS_SELECTOR, RESULTS_ROW)
        except (WebDriverException, TimeoutException):
            return None

    def _await_replaced(self, anchor) -> bool:
        """The wait every filter postback must use: results *replaced*, not
        merely present.

        A presence wait cannot work here and never could. The portal leaves the
        previous result rows in the DOM until the postback swaps them in, so
        "is there a row on the page?" is true the instant it is asked — on the
        **old, unfiltered** page. The caller then reads that page as if the
        filter had landed.

        Measured, not theorised: a run with a Published Date window of
        08/04–08/10 exported six solicitations published 06/15 to 08/03, every
        one of them from the run's *first* keyword. The panel had reported the
        window applied and verified — it was — but `collect_links` had already
        read the pre-filter rows.

        Watching a node from the current results go stale is the only signal
        that the swap actually happened, which is what `_apply_excluded_keywords`
        always did and every other panel did not.

        Returns whether the swap was observed.
        """
        return self._await_refresh(anchor)

    def _await_refresh(self, anchor) -> bool:
        """Wait for the results to actually be replaced, then settle.

        Returns whether the replacement was observed. Callers that *require* the
        postback to have happened — pressing Apply is one — use that to tell
        "the portal accepted this" from "the click went nowhere". It used to
        return nothing, so a click that never posted was indistinguishable from
        one that did.

        No anchor means the page had no rows to go stale — a search that matched
        nothing, which is a valid state and not a failed apply, so it reports
        True.
        """
        if anchor is None:
            self._await_postback()
            return True
        try:
            WebDriverWait(self.driver, POSTBACK_TIMEOUT).until(EC.staleness_of(anchor))
        except (TimeoutException, WebDriverException):
            # The portal can also answer a filter that changes nothing without
            # re-rendering, so this is reported to the caller rather than raised.
            self._await_postback()
            return False
        self._await_postback()
        return True

    def _click_apply(self, button_id: str) -> bool:
        """Press a panel's Apply button, un-disabling it first. False when the
        button is not on the page — the panel's settings then never posted.

        A date panel's button ships `class="… disabled"` / `aria-disabled="true"`
        and the page only enables it once its own validation is happy; since we
        set the fields programmatically that never fires, so the flags are
        cleared before the click. The Keywords panel's button is not disabled to
        begin with, and clearing flags it does not carry is harmless.
        """
        result = self.driver.execute_script(
            """
            const button = document.getElementById(arguments[0]);
            if (!button) return null;
            // Whether the page's own validation had already enabled it. When it
            // had not, the click below is us overriding the panel rather than
            // the panel agreeing — worth knowing, because a commandButton built
            // with enabled:"false" can ignore a click that only looks allowed.
            const wasDisabled =
              button.classList.contains('disabled') ||
              button.getAttribute('aria-disabled') === 'true' ||
              button.disabled === true;
            button.classList.remove('disabled');
            button.removeAttribute('aria-disabled');
            button.disabled = false;
            button.click();
            // The widget binds through jQuery; a native click reaches those
            // handlers, but trigger explicitly too in case the plugin latched
            // its own state at construction (enabled:"false").
            if (window.jQuery) {
              try { jQuery(button).trigger('click'); } catch (e) {}
            }
            return {was_disabled: wasDisabled};
            """,
            button_id,
        )
        if result is None:
            return False
        if result.get("was_disabled"):
            logger.info(
                "[bidnet] %s was still disabled when applied — the panel's own "
                "validation had not enabled it", button_id,
            )
        return True

    # -- option harvesting -------------------------------------------------

    def _harvest_inline(self, section: Section) -> list[dict[str, str]]:
        try:
            return self.driver.execute_script(_JS_HARVEST, section.panel_body_id) or []
        except WebDriverException:
            return []

    def _harvest_view_all(self, section: Section) -> list[dict[str, str]]:
        """Open a panel's "View All" lightbox and read every option out of it.

        The link posts into `#savedSearchDialogContainer` over AJAX, so the
        dialog appears in place rather than as a navigation. Returns [] when it
        does not open — the caller falls back to the inline slice.
        """
        try:
            link = self.driver.find_element(By.ID, section.view_all_id)
        except WebDriverException:
            return []
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
            self.driver.execute_script("arguments[0].click();", link)
        except WebDriverException:
            return []

        container = "#savedSearchDialogContainer"
        try:
            WebDriverWait(self.driver, POSTBACK_TIMEOUT).until(
                lambda d: d.execute_script(
                    "const n = document.querySelector(arguments[0]);"
                    "return !!n && n.querySelectorAll('[data-filter-item-value]').length > 0;",
                    container,
                )
            )
        except TimeoutException:
            logger.info("[bidnet] View All did not open for %s", section.name)
            self._close_dialog()
            return []

        try:
            options = self.driver.execute_script(_JS_HARVEST_DIALOG, container) or []
        except WebDriverException:
            options = []
        self._close_dialog()
        return options

    def _close_dialog(self) -> None:
        """Dismiss the lightbox so the next panel's link is clickable again."""
        self.driver.execute_script(
            """
            const close = document.querySelector(
              '.ui-dialog-titlebar-close, #savedSearchDialogContainer .closeLink, .mets-dialog-close'
            );
            if (close) { close.click(); return; }
            const container = document.querySelector('#savedSearchDialogContainer');
            if (container) container.innerHTML = '';
            """
        )
        time.sleep(1)

    # -- shared ------------------------------------------------------------

    def _await_postback(self) -> None:
        """Wait out a filter postback: until rows render, or the page says there
        are none.

        Waiting only on rows made an empty result the slowest case there is —
        the wait could never be satisfied, so it burned the full timeout before
        carrying on. A run whose filters legitimately matched nothing paid that
        on every keyword: 60s each, over 20 minutes across a niche, for a page
        that had settled in three seconds.

        The portal states emptiness explicitly, so it is waited on as a real
        outcome rather than inferred from a timeout: the result-group headers
        carry `.solicitationCount`, and every one reading zero means the search
        is answered and empty. The timeout is still the backstop for a page that
        says neither.
        """
        time.sleep(SETTLE_SECONDS)
        try:
            WebDriverWait(self.driver, POSTBACK_TIMEOUT).until(
                lambda d: d.execute_script(_JS_POSTBACK_SETTLED, RESULTS_ROW)
            )
        except (TimeoutException, WebDriverException):
            pass
