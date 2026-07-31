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
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

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
POSTBACK_TIMEOUT = 30
SETTLE_SECONDS = 2

# Results-table row — present once a postback has re-rendered the list. A search
# that legitimately matches nothing has no rows, so waits on it are best-effort.
RESULTS_ROW = "table tbody tr.mets-table-row"

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


class SidebarFilterError(Exception):
    """The sidebar could not be put into the requested state."""


class SidebarDriver:
    """Applies and reads the search sidebar. `note` receives human-readable
    progress/problem lines (the scraper wires it to the run's error list)."""

    def __init__(self, driver, note: Callable[[str], None] | None = None):
        self.driver = driver
        self.note = note or (lambda message: None)

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
        return report

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
        self.driver.execute_script("arguments[0].click();", radio)
        self._await_postback()

    # -- list sections -----------------------------------------------------

    def _apply_bulk(self, wanted: list[tuple[Section, list[str]]]) -> bool:
        """Write every selection into the hidden fields and submit once.

        Returns True when the portal came back with those selections intact.
        """
        updates = [
            {"id": section.hidden_field_id, "value": ",".join(chosen) if chosen else section.empty_value}
            for section, chosen in wanted
        ]
        try:
            result = self.driver.execute_script(_JS_SET_AND_SUBMIT, updates)
        except WebDriverException as exc:
            logger.info("[bidnet] bulk filter submit failed: %s", exc.__class__.__name__)
            return False
        if not result or not result.get("submitted"):
            missing = ", ".join(result.get("missing", [])) if result else "?"
            logger.info("[bidnet] bulk filter submit skipped (missing fields: %s)", missing)
            return False

        self._await_postback()
        return self._verify(wanted)

    def _verify(self, wanted: list[tuple[Section, list[str]]]) -> bool:
        """Re-read the hidden fields; every requested selection must have survived."""
        try:
            values = self.driver.execute_script(
                _JS_READ_FIELDS, [section.hidden_field_id for section, _ in wanted]
            )
        except WebDriverException:
            return False
        for section, chosen in wanted:
            raw = values.get(section.hidden_field_id)
            if raw is None:
                logger.info("[bidnet] %s hidden field gone after submit", section.name)
                return False
            got = {v for v in raw.split(",") if v and v != section.empty_value}
            if got != set(chosen):
                logger.info(
                    "[bidnet] %s did not stick: wanted %s, page has %s",
                    section.name, len(chosen), len(got),
                )
                return False
        return True

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
        try:
            if box.is_selected() == checked:
                return
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", box)
            self.driver.execute_script("arguments[0].click();", box)
        except WebDriverException as exc:
            logger.info("[bidnet] could not toggle %s=%s: %s", section.name, value, exc.__class__.__name__)
            return
        self._await_postback()

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
            if not checkbox.is_selected():
                self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", checkbox)
                self.driver.execute_script("arguments[0].click();", checkbox)

            if value.type == "WITHIN":
                self._set_select(f"{section}WITHIN", value.within)
            elif value.type == "DAY":
                self._set_date_input(f"{section}DAY", value.day or "")
            elif value.type == "RANGE":
                self._set_date_input(f"{section}RANGE1", value.range_start or "")
                self._set_date_input(f"{section}RANGE2", value.range_end or "")

            self._click_apply(f"{section}SearchButton")
        except WebDriverException as exc:
            self.note(f"{name}: could not apply the date filter ({exc.__class__.__name__}).")
            return False

        self._await_postback()
        return True

    def _set_select(self, element_id: str, value: str) -> None:
        """Set a <select> and fire `change` — the panel's own handler listens for
        it to re-enable the Apply button."""
        self.driver.execute_script(
            """
            const select = document.getElementById(arguments[0]);
            if (!select) return;
            select.disabled = false;
            select.value = arguments[1];
            select.dispatchEvent(new Event('change', {bubbles: true}));
            """,
            element_id,
            value,
        )

    def _set_date_input(self, element_id: str, text: str) -> None:
        """Write a mm/dd/yyyy date into a datepicker field and its hidden twin.

        The visible input is `readonly`/`disabled` until its checkbox is ticked
        and carries `onchange="updateDateStatus(...)"`, so the value is assigned
        directly and `change` is dispatched by hand.
        """
        self.driver.execute_script(
            """
            const input = document.getElementById(arguments[0]);
            if (!input) return;
            input.disabled = false;
            input.readOnly = false;
            input.value = arguments[1];
            const hidden = document.getElementById(arguments[0] + '_hidden');
            if (hidden) hidden.value = arguments[1];
            input.dispatchEvent(new Event('change', {bubbles: true}));
            """,
            element_id,
            text,
        )

    def _click_apply(self, button_id: str) -> None:
        """Press a date panel's Apply button, un-disabling it first.

        It ships `class="… disabled"` / `aria-disabled="true"` and the page only
        enables it once its own validation is happy; since we set the fields
        programmatically that never fires, so the flags are cleared before the
        click.
        """
        self.driver.execute_script(
            """
            const button = document.getElementById(arguments[0]);
            if (!button) return;
            button.classList.remove('disabled');
            button.removeAttribute('aria-disabled');
            button.disabled = false;
            button.click();
            """,
            button_id,
        )

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
        """Wait out a filter postback. Best-effort: a filter combination that
        matches nothing renders no rows, which is a valid outcome, not an error."""
        time.sleep(SETTLE_SECONDS)
        try:
            WebDriverWait(self.driver, POSTBACK_TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, RESULTS_ROW))
            )
        except (TimeoutException, WebDriverException):
            pass
