"""Pydantic schemas for the PPRA scraper API."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class PpraFilters(BaseModel):
    keyword: str | None = None
    tender_number: str | None = None
    closing_date: str | None = None
    tender_type: str | None = "ALL"
    category: str | None = "ALL"
    sector: str | None = "ALL"
    nature: str | None = "ALL"
    organization: str | None = None
    country: str | None = None
    advertise_from: str | None = None
    advertise_to: str | None = None
    status: str | None = "ALL"
    city: str | None = None


class PpraOptions(BaseModel):
    apply_closing_filter: bool = True
    deep_detail: bool = False
    delay_ms: int | None = None


class PpraScrapeRequest(BaseModel):
    filters: PpraFilters = Field(default_factory=PpraFilters)
    options: PpraOptions = Field(default_factory=PpraOptions)
    live_preview: bool = False


class PpraBidOut(BaseModel):
    id: int
    run_id: str
    page_no: int
    position: int
    tender_no: str
    title: str
    description: str | None = None
    sector: str | None = None
    tender_ref_no: str | None = None
    tender_type: str | None = None
    category: str | None = None
    nature: str | None = None
    organization: str | None = None
    department: str | None = None
    city: str | None = None
    country: str | None = None
    status: str | None = None
    advertised_on: str | None = None
    closing_at: str | None = None
    detail_url: str | None = None
    invoice_url: str | None = None
    change_status: str
    diff: dict[str, Any] | None = None
    scraped_at: str | None = None


class PpraFilterOptionOut(BaseModel):
    kind: str
    value: str
    label: str
    sort_order: int = 0

