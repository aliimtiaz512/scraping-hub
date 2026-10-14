"""Pure HTML parsing functions for PPRA active tenders."""

import hashlib
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

PKT = ZoneInfo("Asia/Karachi")


def parse_total(html: str) -> tuple[int, int]:
    """Parse 'Showing 50 of 2221' or 'Page 1 of 45' from the listing HTML.
    Returns (total_records, total_pages). Defaults to (0, 1) if not found.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    total_records = 0
    total_pages = 1

    # e.g. "Showing 1 to 50 of 2221 entries" or "Showing 50 of 2221"
    m_records = re.search(r"of\s+([0-9,]+)\s*(?:entries|records|tenders)?", text, re.IGNORECASE)
    if m_records:
        try:
            total_records = int(m_records.group(1).replace(",", ""))
        except ValueError:
            pass

    # e.g. "Page 1 of 45"
    m_pages = re.search(r"Page\s+\d+\s+of\s+([0-9,]+)", text, re.IGNORECASE)
    if m_pages:
        try:
            total_pages = int(m_pages.group(1).replace(",", ""))
        except ValueError:
            pass
    elif total_records > 0:
        total_pages = max(1, (total_records + 49) // 50)

    return total_records, total_pages


def compute_content_hash(data: dict[str, Any]) -> str:
    """Compute sha256 hash over normalized tender fields."""
    norm = {
        "title": (data.get("title") or "").strip().lower(),
        "description": (data.get("description") or "").strip().lower(),
        "status": (data.get("status") or "").strip().lower(),
        "closing_at": str(data.get("closing_at") or "").strip(),
        "organization": (data.get("organization") or "").strip().lower(),
        "department": (data.get("department") or "").strip().lower(),
        "city": (data.get("city") or "").strip().lower(),
        "tender_ref_no": (data.get("tender_ref_no") or "").strip().lower(),
    }
    raw = json.dumps(norm, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_listing(html: str, base_url: str = "https://epms.ppra.gov.pk") -> list[dict[str, Any]]:
    """Parse table rows from PPRA active tenders page HTML.
    Handles cb-rt-main and cb-rt-detail row pairs accurately using data-label attributes.
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # Map detail rows by id if present (e.g. id="cb-rt-detail-23" or by index)
    detail_rows_by_id: dict[str, Any] = {}
    for d_row in soup.find_all("tr", class_="cb-rt-detail"):
        row_id = d_row.get("id", "")
        if row_id:
            detail_rows_by_id[row_id] = d_row

    # Find all main rows
    main_rows = soup.find_all("tr", class_="cb-rt-main")

    # If no cb-rt-main class found, fall back to table row scanning
    if not main_rows:
        table = soup.find("table")
        if table:
            main_rows = [r for r in table.find_all("tr") if r.find("td", attrs={"data-label": True}) or len(r.find_all("td")) >= 5]

    for idx, row in enumerate(main_rows, start=1):
        # 1. Tender No
        tender_no = None
        tender_no_cell = row.find("td", attrs={"data-label": "Tender No"}) or (row.find_all("td")[2] if len(row.find_all("td")) > 2 else None)
        if tender_no_cell:
            strong_no = tender_no_cell.find("strong")
            if strong_no:
                tender_no = strong_no.get_text(strip=True)
            else:
                m_t = re.search(r"\b(TS[0-9A-Z]+)\b", tender_no_cell.get_text(), re.IGNORECASE)
                if m_t:
                    tender_no = m_t.group(1).upper()

        if not tender_no:
            # Try finding any TS pattern across row
            m_t = re.search(r"\b(TS[0-9A-Z]+)\b", row.get_text(), re.IGNORECASE)
            tender_no = m_t.group(1).upper() if m_t else f"PPRA-{idx}"

        # 2. Details cell (Title, description, sector badge, tender ref no)
        title = ""
        description = None
        sector = None
        tender_ref_no = None

        details_cell = row.find("td", attrs={"data-label": "Tender Details"}) or (row.find_all("td")[3] if len(row.find_all("td")) > 3 else None)
        if details_cell:
            strong_title = details_cell.find("strong")
            if strong_title:
                title = strong_title.get_text(strip=True)
            small_desc = details_cell.find("small", class_="text-muted")
            if small_desc:
                description = small_desc.get_text(strip=True)
            
            # Badges inside details cell
            badges = details_cell.find_all(class_="badge")
            if len(badges) >= 1:
                sector = badges[0].get_text(strip=True)
            if len(badges) >= 2:
                tender_ref_no = badges[1].get_text(strip=True)

        # 3. Status
        status = "Published"
        status_cell = row.find("td", attrs={"data-label": "Status"}) or (row.find_all("td")[4] if len(row.find_all("td")) > 4 else None)
        if status_cell:
            status = status_cell.get_text(" ", strip=True) or "Published"

        # 4. Advertised date
        advertised_on = None
        adv_cell = row.find("td", attrs={"data-label": "Advertised"}) or (row.find_all("td")[5] if len(row.find_all("td")) > 5 else None)
        if adv_cell:
            advertised_on = adv_cell.get_text(strip=True) or None

        # 5. Closing datetime
        closing_at = None
        closing_cell = row.find("td", attrs={"data-label": "Closing"}) or (row.find_all("td")[6] if len(row.find_all("td")) > 6 else None)
        if closing_cell:
            c_date_el = closing_cell.find("strong")
            c_time_el = closing_cell.find("small")
            c_date = c_date_el.get_text(strip=True) if c_date_el else ""
            c_time = c_time_el.get_text(strip=True) if c_time_el else ""
            if c_date and c_time:
                closing_at = f"{c_date} {c_time}"
            elif c_date:
                closing_at = c_date
            else:
                closing_at = closing_cell.get_text(" ", strip=True) or None

        # 6. Action Links
        detail_url = None
        invoice_url = None
        for a in row.find_all("a", href=True):
            href = a["href"]
            if "tender-details" in href:
                detail_url = urljoin(base_url, href)
            elif "invoice" in href:
                invoice_url = urljoin(base_url, href)

        # 7. Detail row info (Organization, Category, Nature, City)
        organization = None
        department = None
        city = None
        country = "Pakistan"
        tender_type = "Tender Notice"
        category = "Goods"
        nature = "Local"

        # Locate corresponding detail row
        expand_btn = row.find("button", class_="cb-rt-expand")
        data_id = expand_btn.get("data-id") if expand_btn else None
        d_row = None
        if data_id:
            d_row = detail_rows_by_id.get(f"cb-rt-detail-{data_id}")
        if not d_row:
            next_sibling = row.find_next_sibling("tr")
            if next_sibling and "cb-rt-detail" in next_sibling.get("class", []):
                d_row = next_sibling

        if d_row:
            # Parse key-value pairs from .cb-rt-grid
            for block in d_row.find_all("div"):
                label_el = block.find("span", class_="cb-rt-dl")
                val_el = block.find("span", class_="cb-rt-dv")
                if not label_el or not val_el:
                    continue
                label_txt = label_el.get_text(strip=True).lower()
                val_txt = val_el.get_text(" ", strip=True)

                if "organization" in label_txt:
                    org_strong = val_el.find("strong")
                    if org_strong:
                        organization = org_strong.get_text(strip=True)
                    else:
                        organization = val_txt
                    # Look for city/country
                    map_pin = val_el.find(class_="ri-map-pin-line")
                    if map_pin and map_pin.parent:
                        loc_txt = map_pin.parent.get_text(strip=True)
                        if "-" in loc_txt:
                            parts = loc_txt.split("-")
                            city = parts[0].strip()
                            country = parts[1].strip()
                        else:
                            city = loc_txt.strip()
                elif "tender type" in label_txt:
                    tender_type = val_txt
                elif "procurement category" in label_txt or "category" in label_txt:
                    category = val_txt
                elif "tender nature" in label_txt or "nature" in label_txt:
                    nature = val_txt
                elif "sector" in label_txt and not sector:
                    sector = val_txt

        col_texts = [c.get_text(" ", strip=True) for c in row.find_all("td")]

        item: dict[str, Any] = {
            "position": idx,
            "tender_no": tender_no,
            "title": title or f"Tender {tender_no}",
            "description": description,
            "sector": sector,
            "tender_ref_no": tender_ref_no,
            "tender_type": tender_type,
            "category": category,
            "nature": nature,
            "organization": organization,
            "department": department,
            "city": city,
            "country": country,
            "status": status,
            "advertised_on": advertised_on,
            "closing_at": closing_at,
            "detail_url": detail_url,
            "invoice_url": invoice_url,
            "raw_data": {"columns": col_texts},
        }
        item["content_hash"] = compute_content_hash(item)
        results.append(item)

    return results


def parse_options(html: str) -> dict[str, list[dict[str, str]]]:
    """Parse dropdown options from the PPRA search form HTML."""
    soup = BeautifulSoup(html, "html.parser")
    options_by_kind: dict[str, list[dict[str, str]]] = {}

    for select in soup.find_all("select"):
        name = select.get("name") or select.get("id") or ""
        kind = name.lower()
        if not kind:
            continue

        opts = []
        for opt in select.find_all("option"):
            val = opt.get("value", "").strip()
            lbl = opt.get_text(strip=True)
            if val and val != "0" and val.upper() != "ALL":
                opts.append({"value": val, "label": lbl})

        if opts:
            options_by_kind[kind] = opts

    return options_by_kind

