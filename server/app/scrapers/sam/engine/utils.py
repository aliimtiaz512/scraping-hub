"""
SAM.gov Scraper — utility functions.

Standalone helpers for date parsing, field extraction, filtering, and debugging.
Extracted from SAMGovScraper to keep the main class focused on orchestration.
"""

import re
from datetime import datetime

from bs4 import BeautifulSoup


# ── Compiled date patterns ──────────────────────────────────────────────────

DATE_PATTERN = re.compile(r"[A-Z][a-z]{2}\s+\d{1,2},\s*\d{4}")

ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

SLASH_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")

FULL_MONTH_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(\d{1,2}),?\s*(\d{4})"
)


# ── Date utilities ──────────────────────────────────────────────────────────


def parse_any_date(s: str) -> str:
    """
    Extract a date from ANY format and normalise it to "Mon D, YYYY".

    Handles all formats SAM.gov (or the user's browser timezone
    conversion) may produce, including but not limited to:
      "Mar 17, 2026 2:26 PM GMT+7"   -> "Mar 17, 2026"
      "2026-03-31T17:00:00+05:30"    -> "Mar 31, 2026"
      "2026-03-31"                   -> "Mar 31, 2026"
      "03/31/2026"                   -> "Mar 31, 2026"
      "March 31, 2026"               -> "Mar 31, 2026"
      "Mar 31, 2026"                 -> "Mar 31, 2026"  (unchanged)

    Returns "" if no date pattern can be found in s.
    """
    if not s:
        return ""

    # 1: Already in "Mon DD, YYYY" form (with optional time/tz suffix)
    m = DATE_PATTERN.search(s)
    if m:
        return m.group().strip()

    # 2: ISO 8601  "2026-03-31T17:00:00+05:30"  or bare "2026-03-31"
    m = ISO_DATE_RE.search(s)
    if m:
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return f"{d.strftime('%b')} {d.day}, {d.year}"
        except ValueError:
            pass

    # 3: MM/DD/YYYY  "03/31/2026"
    m = SLASH_DATE_RE.search(s)
    if m:
        try:
            d = datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)))
            return f"{d.strftime('%b')} {d.day}, {d.year}"
        except ValueError:
            pass

    # 4: Full month name  "March 31, 2026"
    m = FULL_MONTH_RE.search(s)
    if m:
        try:
            d = datetime.strptime(
                f"{m.group(1)} {int(m.group(2))} {m.group(3)}", "%B %d %Y"
            )
            return f"{d.strftime('%b')} {d.day}, {d.year}"
        except ValueError:
            pass

    return ""


def looks_like_date(s: str) -> bool:
    """
    Return True when s contains a recognisable date token (Mon DD, YYYY).
    Used to guard date fields against being contaminated with URLs or
    other non-date text that fallback strategies sometimes return.
    """
    return bool(DATE_PATTERN.search(s)) if s else False


def clean_updated_date(date_str: str) -> str:
    """
    Strip the version count suffix from an Updated Date string so only the
    bare date is stored in the CSV column.

    Examples:
      "Mar 17, 2026 (1)"  →  "Mar 17, 2026"
      "Mar 17, 2026"      →  "Mar 17, 2026"   (unchanged)
    """
    if not date_str:
        return date_str
    return re.sub(r"\s*\(\d+\)\s*", "", date_str).strip()


def matches_date_range(
    date_str: str,
    filter_date_from: datetime | None,
    filter_date_to: datetime | None,
) -> bool:
    """
    Returns True if the extracted Published Date falls within the active
    date range [filter_date_from, filter_date_to] (both ends inclusive).

    Scenarios:
      • from == to (exact day)   → same as old exact-match behaviour
      • from < to  (range)       → any date in the range is accepted
      • Only from set, no to     → from <= date <= today
      • No filter active         → always True (keep all bids)

    Robustness rules:
      • No filter active OR date_str is empty  → True (keep the bid)
      • Date string in unrecognised format      → True (keep to avoid silent drops)
      • Date parsed successfully                → range comparison
    """
    if not filter_date_from or not date_str:
        return True

    normalised = parse_any_date(date_str)
    if not normalised:
        return True

    raw = re.sub(r"\s+", " ", normalised).strip()
    for fmt in ("%b %d, %Y", "%b %d,%Y"):
        try:
            extracted = datetime.strptime(raw, fmt).date()
            from_date = filter_date_from.date()
            to_date = filter_date_to.date() if filter_date_to else datetime.now().date()
            return from_date <= extracted <= to_date
        except ValueError:
            continue

    return True


def check_updated_date_rule(date_str: str, threshold: int = 1) -> bool:
    """
    Returns True (keep) / False (skip).

    Only checks the version/amendment count.
    Threshold = 1 → keep bids with no count (version 0) or count = 1.
    Any higher count → skip.
    """
    if not date_str:
        return True
    version_match = re.search(r"\((\d+)\)", date_str)
    if version_match and int(version_match.group(1)) > threshold:
        return False
    return True


# ── Filtering utilities ─────────────────────────────────────────────────────


def is_valid_title(title: str, forbidden_keywords: list[str]) -> bool:
    """Returns False if the title contains any forbidden keyword."""
    if not title:
        return True
    lower = title.lower()
    for kw in forbidden_keywords:
        if kw in lower:
            return False
    return True


def should_skip_bid(data: dict, skip_conditions: dict) -> tuple[bool, str]:
    """
    Returns (True, reason) if the bid should be discarded.
    Applied AFTER all 9 fields have been extracted.

    Conditions:
      • Department/Ind. Agency contains DoD term  → skip
      • Subtier contains DoD term                 → skip
      • Office contains DLA term                  → skip
    """
    dept = data.get("Department/Ind. Agency", "").lower()
    for term in skip_conditions.get("department_skip_terms", []):
        if term in dept:
            return True, f"Dept=DoD ({dept})"

    subtier = data.get("Subtier", "").lower()
    for term in skip_conditions.get("subtier_skip_terms", []):
        if term in subtier:
            return True, f"Subtier=DoD ({subtier})"

    office = data.get("Office", "").lower()
    for term in skip_conditions.get("office_skip_terms", []):
        if term in office:
            return True, f"Office=DLA ({office})"

    return False, ""


# ── HTML / BS4 utilities ────────────────────────────────────────────────────


def find_field(soup: BeautifulSoup, label: str) -> str:
    """Generic BS4 label-text field search (last-resort fallback)."""
    label_tag = soup.find(
        "label", string=lambda x: x and label.lower() in x.lower()
    )
    if label_tag:
        sib = label_tag.find_next_sibling()
        if sib:
            return sib.get_text(strip=True)
        inp = label_tag.find_next("input")
        if inp:
            return inp.get("value", "")

    element = soup.find(
        string=lambda x: x and x.strip().lower() == label.lower()
    )
    if element:
        parent = element.parent
        next_el = parent.find_next_sibling()
        if next_el:
            text = next_el.get_text(strip=True)
            if text:
                return text
        grand = parent.parent
        if grand:
            full_text = grand.get_text(" ", strip=True)
            if label in full_text:
                val = full_text.split(label)[1].strip()
                return val[:50].strip()

    return ""


def save_debug(driver, filename: str) -> None:
    """Write current page source to a debug file."""
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception:
        pass
