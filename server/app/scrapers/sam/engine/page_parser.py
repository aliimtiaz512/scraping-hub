"""
SAM.gov Scraper — search-results page card parser.

Extracts candidate bid links from SAM.gov search-result cards,
applying card-level filters (forbidden titles, version count,
Published Date match, DoD/DLA skip).
"""

import re
import logging

from selenium.webdriver.common.by import By

try:
    from .utils import (
        is_valid_title, check_updated_date_rule, matches_date_range,
        save_debug,
    )
except ImportError:
    from utils import (
        is_valid_title, check_updated_date_rule, matches_date_range,
        save_debug,
    )

logger = logging.getLogger(__name__)


def get_links_from_current_page(
    driver,
    selectors: dict,
    date_cfg: dict,
    filtering: dict,
    skip_cond: dict,
    filter_date_obj,
    filter_date_from,
    filter_date_to,
    debug_cfg: dict,
) -> list[dict]:
    """
    Parse search-result cards and return a list of candidate bid dicts.

    Each dict contains:
      - url: str
      - title: str
      - pre_extracted_updated_date: str
      - card_pub_date: str
      - bid_repeat_count: int

    Card-level filters applied:
      1. Forbidden title keywords
      2. Version count > threshold
      3. Published Date exact-day match (when date filter is active)
      4. DoD / DLA skip terms
    """
    candidates = []
    try:
        # Find card elements
        cards = []
        for sel in selectors.get("card_selectors", [".sds-card"]):
            cards = driver.find_elements(By.CSS_SELECTOR, sel)
            if cards:
                logger.info(f"Found {len(cards)} cards via selector: {sel}")
                break

        if not cards:
            logger.info("No cards found on page.")
            save_debug(driver, debug_cfg.get("no_cards_file", "debug_no_cards.html"))
            return []

        title_min_len = selectors.get("title_min_length", 10)
        href_contains = selectors.get("title_href_contains", "opp")
        updated_label = date_cfg.get("updated_date_card_label", "Updated Date")

        for card in cards:
            try:
                # ── Find title element ──────────────────────────────
                title_elem = None
                for ts in selectors.get("title_selectors", []):
                    try:
                        el = card.find_element(By.CSS_SELECTOR, ts)
                        if el and el.is_displayed():
                            title_elem = el
                            break
                    except Exception:
                        continue

                if not title_elem:
                    for lnk in card.find_elements(By.TAG_NAME, "a"):
                        href = lnk.get_attribute("href") or ""
                        if len(lnk.text) > title_min_len and href_contains in href:
                            title_elem = lnk
                            break

                if not title_elem:
                    continue

                title = title_elem.text.strip()
                url = title_elem.get_attribute("href")

                # ── Filter 1: forbidden title ───────────────────────
                forbidden = filtering.get("forbidden_titles", [])
                if not is_valid_title(title, forbidden):
                    logger.info(f"[SKIP] Forbidden title: {title}")
                    continue

                # ── Extract Updated Date + Published Date from card ──
                card_text = ""
                updated_date = ""
                card_pub_date = ""
                try:
                    card_text = card.text

                    # Updated Date – prefer DOM element with sds-field__value
                    updated_date = ""
                    try:
                        lbl_els = card.find_elements(
                            By.XPATH,
                            f".//*[contains(@class,'sds-field__label') and "
                            f"normalize-space(text())='{updated_label}']",
                        )
                        for lbl in lbl_els:
                            try:
                                val_el = lbl.find_element(
                                    By.XPATH,
                                    "following-sibling::*[contains(@class,'sds-field__value')]",
                                )
                                t = val_el.text.strip()
                                if t:
                                    updated_date = t
                                    break
                            except Exception:
                                pass
                            if not updated_date:
                                try:
                                    val_el = lbl.find_element(
                                        By.XPATH,
                                        "../*[contains(@class,'sds-field__value')]",
                                    )
                                    t = val_el.text.strip()
                                    if t:
                                        updated_date = t
                                        break
                                except Exception:
                                    pass
                    except Exception:
                        pass

                    # Fallback: plain text-split
                    if not updated_date and updated_label in card_text:
                        updated_date = (
                            card_text.split(updated_label)[1]
                            .strip().split("\n")[0].strip()
                        )

                    # ── Bid Repeat Count ──────────────────────────────
                    bid_repeat_count = 0
                    try:
                        count_els = card.find_elements(
                            By.CSS_SELECTOR, "a.ng-star-inserted"
                        )
                        for ce in count_els:
                            m = re.match(r"^\((\d+)\)$", ce.text.strip())
                            if m:
                                bid_repeat_count = int(m.group(1))
                                break
                    except Exception:
                        pass

                    # ── Published Date: CSS class extraction ──────────
                    pub_label = selectors.get(
                        "card_pub_date_label",
                        date_cfg.get("published_date_card_label", "Published Date"),
                    )
                    label_cls = selectors.get("card_field_label_class", "sds-field__label")
                    value_cls = selectors.get("card_field_value_class", "sds-field__value")

                    # Strategy A
                    try:
                        val_el = card.find_element(
                            By.XPATH,
                            f".//*[contains(@class,'{label_cls}') "
                            f"and normalize-space(text())='{pub_label}']"
                            f"/following-sibling::*[contains(@class,'{value_cls}')]",
                        )
                        card_pub_date = val_el.text.strip()
                    except Exception:
                        pass

                    # Strategy B
                    if not card_pub_date:
                        try:
                            val_el = card.find_element(
                                By.XPATH,
                                f".//*[contains(@class,'{label_cls}') "
                                f"and normalize-space(text())='{pub_label}']"
                                f"/../*[contains(@class,'{value_cls}')]",
                            )
                            card_pub_date = val_el.text.strip()
                        except Exception:
                            pass

                    # Strategy C: text-split fallback
                    if not card_pub_date and pub_label in card_text:
                        card_pub_date = (
                            card_text.split(pub_label)[1]
                            .strip().split("\n")[0].strip()
                        )

                except Exception:
                    pass

                # ── Filter 2: version count ──────────────────────────
                threshold = filtering.get("version_count_threshold", 1)
                if not check_updated_date_rule(updated_date, threshold):
                    logger.info(f"[SKIP] Date/version rule: {updated_date} | {title}")
                    continue

                # ── Filter 3: Published Date exact-day match (CARD) ──
                if filter_date_obj:
                    if not card_pub_date:
                        logger.info(
                            f"[SKIP-CARD] Published Date not found on card "
                            f"(filter active – cannot confirm date) | {title}"
                        )
                        continue
                    if not matches_date_range(card_pub_date, filter_date_from, filter_date_to):
                        logger.info(
                            f"[SKIP-CARD] Published {card_pub_date} "
                            f"!= {filter_date_obj.date()} | {title}"
                        )
                        continue

                # ── Filter 4: DoD / DLA check at CARD level ─────────
                if card_text:
                    card_lower = card_text.lower()
                    _dod_skip = False
                    _dod_reason = ""

                    for term in skip_cond.get("department_skip_terms", []):
                        if term in card_lower:
                            _dod_skip = True
                            _dod_reason = f"Dept contains '{term}'"
                            break

                    if not _dod_skip:
                        for term in skip_cond.get("subtier_skip_terms", []):
                            if term in card_lower:
                                _dod_skip = True
                                _dod_reason = f"Subtier contains '{term}'"
                                break

                    if not _dod_skip:
                        for term in skip_cond.get("office_skip_terms", []):
                            if term in card_lower:
                                _dod_skip = True
                                _dod_reason = f"Office contains '{term}'"
                                break

                    if _dod_skip:
                        logger.info(
                            f"[SKIP-CARD] DoD/DLA ({_dod_reason}) – "
                            f"detail page NOT opened | {title}"
                        )
                        continue

                candidates.append({
                    "url":                        url,
                    "title":                      title,
                    "pre_extracted_updated_date": updated_date,
                    "card_pub_date":              card_pub_date,
                    "bid_repeat_count":           bid_repeat_count,
                })

            except Exception:
                continue

    except Exception as e:
        logger.error(f"Error collecting page links: {e}")
        save_debug(driver, debug_cfg.get("error_file", "debug_error.html"))

    return candidates
