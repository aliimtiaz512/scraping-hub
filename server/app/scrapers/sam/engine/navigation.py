"""
SAM.gov Scraper — page navigation, URL building, and UI filter helpers.

Functions for building search URLs, verifying pagination state, clicking
the Next button, and filling SAM.gov's date-picker and NAICS inputs.
"""

import re
import time
import logging
from datetime import datetime

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger(__name__)


# ── URL builder ─────────────────────────────────────────────────────────────

def build_page_url(
    base_url: str,
    page: int,
    naics_codes: list[str],
    filter_date_from: datetime | None,
    filter_date_to: datetime | None,
    url_date_params: dict,
    filter_date_obj: datetime | None = None,
    award_notice: bool = False,
    award_notice_param: str = "",
) -> str:
    """
    Build the full SAM.gov search URL for a given page number.

    Appends server-side updatedDate range, NAICS code params, and optionally
    the Award Notice notice-type filter when award_notice=True.
    """
    url = base_url.format(page=page)

    # Award Notice filter — appended only when user explicitly enables it
    if award_notice and award_notice_param:
        url += award_notice_param
        logger.debug("Award Notice filter appended to URL")

    # NAICS code URL params
    if naics_codes:
        for code in naics_codes:
            url += f"&sfm%5BnaicsCodes%5D%5B%5D={code}"

    if filter_date_from:
        from_iso = filter_date_from.strftime("%Y-%m-%d")
        to_iso = (
            filter_date_to.strftime("%Y-%m-%d")
            if filter_date_to
            else datetime.now().strftime("%Y-%m-%d")
        )
        url += (
            f"&sfm%5Bdates%5D%5BupdatedDate%5D%5BupdatedDateFrom%5D={from_iso}"
            f"&sfm%5Bdates%5D%5BupdatedDate%5D%5BupdatedDateTo%5D={to_iso}"
        )
        logger.debug(f"URL date range params appended: {from_iso} -> {to_iso} (page {page})")

    # Legacy single-date extra params
    elif filter_date_obj and url_date_params:
        date_iso = filter_date_obj.strftime("%Y-%m-%d")
        for param_template in url_date_params.values():
            url += "&" + param_template.format(date=date_iso)

    return url


# ── Page-number verifier ────────────────────────────────────────────────────

# JavaScript to read the current page number from SAM.gov's pagination widget
_JS_PAGE = """
    var selectors = [
        'sds-pagination input[type="number"]',
        'sds-pagination input',
        'nav[aria-label*="pagination"] input',
        '.sds-pagination input'
    ];
    for (var s = 0; s < selectors.length; s++) {
        var els = document.querySelectorAll(selectors[s]);
        for (var i = 0; i < els.length; i++) {
            var v = parseInt(els[i].value, 10);
            if (!isNaN(v) && v > 0) return v;
        }
    }
    var m = window.location.search.match(/[?&]page=(\\d+)/);
    if (m) return parseInt(m[1], 10);
    return -1;
"""


def verify_on_correct_page(driver, expected_page: int) -> bool:
    """
    Confirm SAM.gov is displaying the expected page number.

    Strategy order:
      1. Python URL check (instant)
      2. Pagination widget with retries (up to 10 s)

    Returns True when confirmed or indeterminate.
    Returns False when both checks consistently show a different page.
    """
    # Step 1: URL check
    try:
        current_url = driver.current_url
        url_m = re.search(r"[?&]page=(\d+)", current_url)
        if url_m:
            url_page = int(url_m.group(1))
            if url_page == expected_page:
                return True
            logger.debug(
                f"URL shows page={url_page}, expected {expected_page}; "
                f"checking widget before deciding."
            )
    except Exception:
        pass

    # Step 2: Pagination widget with retries
    for attempt in range(5):
        try:
            result = driver.execute_script(_JS_PAGE)
            if result is None or int(result) == -1:
                return True
            current = int(result)
            if current == expected_page:
                return True
            if attempt < 4:
                time.sleep(2)
                continue
            logger.info(
                f"SAM.gov shows page {current} after {attempt + 1} checks, "
                f"expected {expected_page} — end of results."
            )
            return False
        except Exception:
            return True

    return True


# ── Next-page navigation ───────────────────────────────────────────────────

def click_next_page(driver, current_page: int, timeouts: dict, selectors: dict) -> bool:
    """
    Click SAM.gov's pagination Next button to load the next page.

    Returns True if the next page loaded (cards present).
    Returns False if button absent/disabled or no cards rendered.
    """
    NEXT_BTN_ID = "bottomPagination-nextPage"
    results_wait = timeouts.get("results_wait", 20)

    # Locate the Next button
    try:
        btn = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, NEXT_BTN_ID))
        )
    except Exception:
        logger.info(
            f"Next-page button '{NEXT_BTN_ID}' not found after page "
            f"{current_page} — end of results."
        )
        return False

    # Check if disabled
    disabled = (
        btn.get_attribute("disabled") is not None
        or btn.get_attribute("aria-disabled") == "true"
        or "disabled" in (btn.get_attribute("class") or "")
    )
    if disabled:
        logger.info(
            f"Next-page button is disabled after page {current_page} "
            f"— end of results."
        )
        return False

    # Scroll and click
    driver.execute_script("arguments[0].scrollIntoView(true);", btn)
    time.sleep(0.5)
    try:
        btn.click()
    except Exception:
        driver.execute_script("arguments[0].click();", btn)

    time.sleep(2)

    # Wait for result cards
    try:
        WebDriverWait(driver, results_wait).until(
            lambda d: d.find_elements(
                By.CSS_SELECTOR,
                selectors.get(
                    "results_container_css",
                    "sds-search-result-list, .sds-card",
                ),
            )
        )
        return True
    except Exception:
        logger.info(
            f"No cards rendered after clicking Next from page {current_page} "
            f"— end of results."
        )
        return False


# ── UI date-range filter ───────────────────────────────────────────────────

def apply_ui_date_filters(
    driver,
    filter_date_from: datetime | None,
    filter_date_to: datetime | None,
    ui_cfg: dict,
) -> bool:
    """
    Fill SAM.gov's Updated Date range pickers after page 1 loads.

    Returns True if at least the from-date field was filled.
    """
    if not filter_date_from:
        return False

    from_id  = ui_cfg.get("from_input_id", "formly_31_datepicker_updatedDateFrom_1")
    to_id    = ui_cfg.get("to_input_id",   "formly_31_datepicker_updatedDateTo_2")
    date_fmt = ui_cfg.get("input_date_format", "%m/%d/%Y")
    wait_sec = ui_cfg.get("apply_wait", 3)

    from_str = filter_date_from.strftime(date_fmt)
    to_str = (
        filter_date_to.strftime(date_fmt)
        if filter_date_to
        else datetime.now().strftime(date_fmt)
    )

    def _fill(input_id: str, date_str: str, key_fragment: str) -> bool:
        el = None
        try:
            el = WebDriverWait(driver, 6).until(
                EC.presence_of_element_located((By.ID, input_id))
            )
        except Exception:
            pass

        if el is None:
            try:
                els = driver.find_elements(
                    By.XPATH, f"//input[contains(@id, '{key_fragment}')]"
                )
                if els:
                    el = els[0]
            except Exception:
                pass

        if el is None:
            logger.warning(f"Date filter input not found: id='{input_id}'")
            return False

        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", el
            )
            time.sleep(0.3)
            el.click()
            time.sleep(0.2)
            el.send_keys(Keys.CONTROL + "a")
            el.send_keys(Keys.DELETE)
            el.send_keys(date_str)
            el.send_keys(Keys.TAB)
            time.sleep(0.3)
            driver.execute_script(
                """
                var e = arguments[0];
                ['input','change','blur'].forEach(function(t){
                    e.dispatchEvent(new Event(t, {bubbles:true}));
                });
                """,
                el,
            )
            return True
        except Exception as exc:
            logger.debug(f"Error filling date input '{input_id}': {exc}")
            return False

    from_ok = _fill(from_id, from_str, "updatedDateFrom")
    to_ok   = _fill(to_id,   to_str,   "updatedDateTo")

    if from_ok:
        time.sleep(wait_sec)
        logger.info(
            f"UI date filters applied: {from_str} -> {to_str} | "
            f"updatedDate(from={'OK' if from_ok else 'FAIL'}, to={'OK' if to_ok else 'FAIL'})"
        )
    else:
        logger.warning(
            "Could not fill UI date filter inputs — "
            "URL params will still apply the server-side range."
        )

    return from_ok


# ── NAICS code filter ──────────────────────────────────────────────────────

def apply_naics_filter(driver, naics_codes: list[str]) -> None:
    """
    Fill SAM.gov's NAICS combobox with each code from the list.
    Expands the accordion, types the code, selects the autocomplete result.
    """
    if not naics_codes:
        return

    # Step 1: Expand the "Product or Service Information" accordion
    try:
        accordion_btn = None
        btns = driver.find_elements(By.CSS_SELECTOR, "button.usa-accordion__button")
        for btn in btns:
            if "product or service" in btn.text.lower():
                accordion_btn = btn
                break

        if accordion_btn:
            expanded = accordion_btn.get_attribute("aria-expanded")
            if expanded != "true":
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", accordion_btn
                )
                time.sleep(0.5)
                accordion_btn.click()
                time.sleep(1)
                logger.info("Expanded 'Product or Service Information' accordion")
            else:
                logger.info("'Product or Service Information' accordion already expanded")
        else:
            logger.warning("Could not find 'Product or Service Information' accordion button")
    except Exception as exc:
        logger.warning(f"Error expanding accordion: {exc}")

    # Step 2: Enter each NAICS code
    for code in naics_codes:
        try:
            el = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "naics"))
            )
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", el
            )
            time.sleep(0.5)
            el.click()
            time.sleep(0.3)
            # Clear existing text cross-platform (Mac + Windows/Linux)
            el.send_keys(Keys.COMMAND + "a")
            el.send_keys(Keys.BACKSPACE)
            el.send_keys(Keys.CONTROL + "a")
            el.send_keys(Keys.BACKSPACE)
            el.clear()
            time.sleep(0.2)
            el.send_keys(code)
            time.sleep(2)

            selected = False
            try:
                option = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "naics-resultItem-0"))
                )
                option.click()
                selected = True
                logger.info(f"NAICS code {code}: selected via resultItem-0")
            except Exception:
                pass

            if not selected:
                try:
                    option = WebDriverWait(driver, 3).until(
                        EC.element_to_be_clickable((
                            By.CSS_SELECTOR, "li.sds-autocomplete__item"
                        ))
                    )
                    option.click()
                    selected = True
                    logger.info(f"NAICS code {code}: selected via sds-autocomplete__item")
                except Exception:
                    pass

            if not selected:
                logger.warning(f"NAICS code {code}: no dropdown item found to click")

            time.sleep(1)

        except Exception as exc:
            logger.warning(f"Failed to apply NAICS code {code}: {exc}")

    time.sleep(3)
    logger.info(f"Applied {len(naics_codes)} NAICS code(s)")


# ── Date-window boundary detector ─────────────────────────────────────────

def is_past_date_window(
    driver,
    filter_date_from: datetime | None,
    date_cfg: dict,
    selectors: dict,
    date_pattern,
) -> bool:
    """
    Returns True when ALL visible cards have Updated Date < filter date.

    SAM.gov sorts by -modifiedDate, so once every card is before the
    filter date, no later page can contain matching bids.
    """
    if not filter_date_from:
        return False

    filter_date = filter_date_from.date()
    updated_label = date_cfg.get("updated_date_card_label", "Updated Date")

    try:
        cards = []
        for sel in selectors.get("card_selectors", [".sds-card"]):
            cards = driver.find_elements(By.CSS_SELECTOR, sel)
            if cards:
                break

        if not cards:
            return False

        dates_found: list = []
        for card in cards:
            try:
                card_text = card.text
                if updated_label not in card_text:
                    continue
                raw = (
                    card_text.split(updated_label)[1]
                    .strip().split("\n")[0].strip()
                )
                raw = re.sub(r"\s*\(\d+\)\s*", "", raw).strip()
                m = date_pattern.search(raw)
                if not m:
                    continue
                date_str = re.sub(r"\s+", " ", m.group()).strip()
                for fmt in ("%b %d, %Y", "%b %d,%Y"):
                    try:
                        dates_found.append(
                            datetime.strptime(date_str, fmt).date()
                        )
                        break
                    except ValueError:
                        continue
            except Exception:
                continue

        if not dates_found:
            return False

        past = all(d < filter_date for d in dates_found)
        if past:
            logger.info(
                f"Date window passed: all {len(dates_found)} card(s) on this "
                f"page have Updated Date < {filter_date} — stopping."
            )
        return past

    except Exception:
        return False
