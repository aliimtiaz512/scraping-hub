"""
SAM.gov Scraper — field & description extractors.

Multi-strategy helpers for reading field values and the Description
section from SAM.gov detail pages.  All functions accept the Selenium
``driver`` and/or BeautifulSoup ``soup`` as explicit arguments so they
can be tested and called without a scraper instance.
"""

import re
import time
import logging

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By

try:
    from .utils import find_field
except ImportError:
    from utils import find_field

logger = logging.getLogger(__name__)


# ── Compiled regex for body-text description extraction ─────────────────────

DESC_RE = re.compile(
    r'\nDescription\s*\n([\s\S]+?)(?=\n(?:'
    r'Contact Information|Contracting Office|Attachments[\s/\-]*Links|'
    r'Place of Performance|History|Award Notices'
    r')|\Z)',
    re.IGNORECASE,
)

# JavaScript injected into the page to collect ONLY the plain-text
# paragraph content that appears beneath the "Description" heading.
JS_DESC_COLLECT = """
    return (function(heading) {
        var HEADING_TAGS = ['H1','H2','H3','H4','H5','H6'];
        var parts = [];

        function collectText(el) {
            var out = [];
            el.childNodes.forEach(function(node) {
                if (node.nodeType === 3) {
                    var t = node.textContent.trim();
                    if (t) out.push(t);
                } else if (node.nodeType === 1) {
                    if (HEADING_TAGS.indexOf(node.tagName) !== -1) return;
                    var t = (node.innerText || node.textContent || '').trim();
                    if (t) out.push(t);
                }
            });
            return out.join(' ').trim();
        }

        var sib = heading.nextElementSibling;
        while (sib) {
            if (HEADING_TAGS.indexOf(sib.tagName) !== -1) break;
            var pEls = sib.querySelectorAll('p, li');
            if (pEls.length) {
                pEls.forEach(function(p) {
                    var t = (p.innerText || p.textContent || '').trim();
                    if (t) parts.push(t);
                });
            } else {
                var t = collectText(sib);
                if (t) parts.push(t);
            }
            sib = sib.nextElementSibling;
        }

        if (!parts.length && heading.nextElementSibling) {
            var container = heading.nextElementSibling;
            var pEls = container.querySelectorAll('p, li');
            if (pEls.length) {
                pEls.forEach(function(p) {
                    var t = (p.innerText || p.textContent || '').trim();
                    if (t) parts.push(t);
                });
            } else {
                var t = collectText(container);
                if (t) parts.push(t);
            }
        }

        return parts.join(' ').trim();
    })(arguments[0]);
"""

# Section headings that mark the end of the description block
_BREAK_WORDS = [
    "\ncontact information",
    "\ncontracting office",
    "\nattachments/links",
    "\nattachments",
    "\nplace of performance",
    "\nhistory",
    "\naward notices",
]


# ── Internal helpers ────────────────────────────────────────────────────────

def _cut_at_next_section(text: str) -> str:
    """Remove everything from the next section heading onward."""
    low = text.lower()
    cutoff = len(text)
    for bw in _BREAK_WORDS:
        idx = low.find(bw)
        if 0 < idx < cutoff:
            cutoff = idx
    return text[:cutoff].strip()


def _strip_heading_prefix(text: str, label: str) -> str:
    """Remove a leading 'Description' label from the top of the text."""
    if text.lower().startswith(label.lower()):
        text = text[len(label):].lstrip(" :\n\r\t-")
    return text.strip()


# ── Public API ──────────────────────────────────────────────────────────────

def get_field(
    soup: BeautifulSoup,
    field_id: str,
    label: str,
    driver,
) -> str:
    """
    Try every known strategy to extract the value for a named field.

    Strategy order:
      1. aria-describedby attribute (BS4)
      2. Direct ID → inner text (BS4)
      3. Direct ID → next sibling (BS4)
      4. Direct ID → parent sibling (BS4)
      5. Selenium: CSS [aria-describedby=field_id]
      6. Selenium: CSS [id=field_id] inner text
      7. XPath label-value pairs (Selenium) using the human label
      8. Definition list dt/dd pairs (BS4)
      9. find_field label-text search (BS4)
     10. Regex on full page text
    """
    # ── BS4 strategies ──────────────────────────────────────────────
    # 1: aria-describedby
    val = soup.find(attrs={"aria-describedby": field_id})
    if val:
        t = val.get_text(strip=True)
        if t:
            return t

    # 2: element with that ID – use its own text content
    el_id = soup.find(id=field_id)
    if el_id:
        t = el_id.get_text(strip=True)
        if t and t.lower() != label.lower():
            return t

    # 3: ID → next sibling
    if el_id:
        sib = el_id.find_next_sibling()
        if sib:
            t = sib.get_text(strip=True)
            if t:
                return t
        # 4: ID → parent → next sibling
        parent = el_id.parent
        if parent:
            ps = parent.find_next_sibling()
            if ps:
                t = ps.get_text(strip=True)
                if t:
                    return t

    # 8: definition list <dt>/<dd>
    if label:
        for dt in soup.find_all("dt"):
            if label.lower() in dt.get_text(strip=True).lower():
                dd = dt.find_next_sibling("dd")
                if dd:
                    t = dd.get_text(strip=True)
                    if t:
                        return t

    # 9: generic label text search (BS4)
    if label:
        found = find_field(soup, label)
        if found:
            return found

    # ── Selenium strategies ─────────────────────────────────────────
    # 5: aria-describedby via CSS
    try:
        els = driver.find_elements(
            By.CSS_SELECTOR, f'[aria-describedby="{field_id}"]'
        )
        for e in els:
            t = e.text.strip()
            if t:
                return t
    except Exception:
        pass

    # 6: element ID via CSS
    try:
        els = driver.find_elements(By.CSS_SELECTOR, f'[id="{field_id}"]')
        for e in els:
            t = e.text.strip()
            if t and t.lower() != label.lower():
                return t
    except Exception:
        pass

    # 7: XPath label→value patterns
    if label:
        label_escaped = label.replace("'", "\\'")
        xpaths = [
            f"//span[normalize-space(text())='{label_escaped}']/following-sibling::span[1]",
            f"//span[normalize-space(text())='{label_escaped}']/parent::*/following-sibling::*[1]",
            f"//dt[contains(normalize-space(text()),'{label_escaped}')]/following-sibling::dd[1]",
            f"//*[contains(@class,'label') and contains(normalize-space(text()),'{label_escaped}')]/following-sibling::*[1]",
            f"//*[contains(@class,'key') and contains(normalize-space(text()),'{label_escaped}')]/following-sibling::*[1]",
            f"//*[normalize-space(text())='{label_escaped}']/parent::*//*[contains(@class,'value')]",
        ]
        for xp in xpaths:
            try:
                els = driver.find_elements(By.XPATH, xp)
                for e in els:
                    t = e.text.strip()
                    if t and t.lower() not in (label.lower(), ""):
                        return t
            except Exception:
                continue

    # 10: regex on full page body text
    if label:
        found = regex_from_page_text(driver, label)
        if found:
            return found

    return ""


def regex_from_page_text(driver, label: str) -> str:
    """
    Scan the rendered page body text for 'Label → Value' proximity patterns.
    Returns the text immediately after the label on the same logical line.
    """
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        escaped = re.escape(label)
        m = re.search(
            escaped + r"[:\s]*([^\n]{2,120})",
            body_text,
            re.IGNORECASE,
        )
        if m:
            candidate = m.group(1).strip()
            if candidate and len(candidate) > 1:
                return candidate
    except Exception:
        pass
    return ""


def regex_date_from_page(driver, label: str, date_regex: str = None) -> str:
    """
    Look for a date pattern (e.g. 'Mar 15, 2026') following the given label
    in the full rendered page text.
    """
    if date_regex is None:
        date_regex = r"([A-Z][a-z]{2}\s\d{1,2},\s\d{4})"
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        escaped_label = re.escape(label)
        m = re.search(
            escaped_label + r".*?" + date_regex,
            body_text,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return ""


def extract_description(
    driver,
    desc_selectors: list[str],
    desc_label: str = "Description",
) -> str:
    """
    Extracts the Description section text from a SAM.gov detail page.

    Strategy order (first result with len > 5 wins):
      1. CSS containers — scroll into view, read Selenium .text
      2. Body-text regex (DESC_RE) after scrolling to 40%
      3. Scroll TO the Description heading, then re-regex
      4. JS sibling-walk from heading element
      5. BS4 heading walk on static HTML
    """
    label_upper = desc_label.upper()

    # ── Strategy 1: CSS containers ──────────────────────────────────
    for sel in desc_selectors:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for e in els:
                try:
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", e
                    )
                    time.sleep(1.0)
                    text = e.text.strip()
                    if not text:
                        continue
                    text = _strip_heading_prefix(text, desc_label)
                    text = _cut_at_next_section(text)
                    if len(text) > 5:
                        return text[:5000]
                except Exception:
                    continue
        except Exception:
            continue

    # ── Strategy 2: Body-text regex after scrolling to 40% ──────────
    try:
        driver.execute_script(
            "window.scrollTo(0, document.body.scrollHeight * 0.4);"
        )
        time.sleep(1.0)
        body_text = driver.find_element(By.TAG_NAME, "body").text
        m = DESC_RE.search(body_text)
        if m:
            candidate = m.group(1).strip()
            if len(candidate) > 5:
                return candidate[:5000]
    except Exception:
        pass

    # ── Strategy 3: Scroll TO the heading, then re-regex ────────────
    try:
        h_el = driver.find_element(
            By.XPATH,
            f"//*[normalize-space(text())='{desc_label}' or "
            f"normalize-space(text())='{label_upper}']",
        )
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior:'smooth', block:'center'});",
            h_el,
        )
        time.sleep(1.5)
        body_text = driver.find_element(By.TAG_NAME, "body").text
        m = DESC_RE.search(body_text)
        if m:
            candidate = m.group(1).strip()
            if len(candidate) > 5:
                return candidate[:5000]
    except Exception:
        pass

    # ── Strategy 4: JS sibling-walk from heading ────────────────────
    heading_xpaths = [
        f"//h2[normalize-space(text())='{desc_label}']",
        f"//h3[normalize-space(text())='{desc_label}']",
        f"//h4[normalize-space(text())='{desc_label}']",
        f"//h2[normalize-space(text())='{label_upper}']",
        f"//h3[normalize-space(text())='{label_upper}']",
        f"//*[contains(@class,'section-title') and normalize-space(text())='{desc_label}']",
    ]
    for xp in heading_xpaths:
        try:
            for h_el in driver.find_elements(By.XPATH, xp):
                try:
                    result = driver.execute_script(JS_DESC_COLLECT, h_el)
                    text = (result or "").strip()
                    if len(text) > 5:
                        return text[:5000]
                except Exception:
                    continue
        except Exception:
            continue

    # ── Strategy 5: BS4 heading walk ────────────────────────────────
    fresh_soup = BeautifulSoup(driver.page_source, "html.parser")
    h_tags = ["h1", "h2", "h3", "h4", "h5"]
    for tag in h_tags:
        for h_el in fresh_soup.find_all(tag):
            if h_el.get_text(strip=True).lower() == desc_label.lower():
                parts: list[str] = []
                sib = h_el.find_next_sibling()
                while sib:
                    if sib.name in h_tags:
                        break
                    for p in sib.find_all(["p", "li"]) or [sib]:
                        t = p.get_text(separator=" ", strip=True)
                        if t:
                            parts.append(t)
                    sib = sib.find_next_sibling()
                result = " ".join(parts).strip()
                if len(result) > 5:
                    return result[:5000]

    return ""
