"use client";

import { Card, Field, SelectField } from "@/components/ui";
import type { PhiladelphiaFilters } from "@/lib/api";

/**
 * PHLContracts' Advanced Search, as a form on this dashboard.
 *
 * The portal's own form sits behind an "Advanced" link and a Document Type
 * dropdown; a run given any of these criteria drives it. The fields are the
 * portal's, in the portal's words, so what someone fills in here is what the
 * scraper types over there.
 *
 * **Short fixed lists are dropdowns; long or changing ones are text.** Status,
 * Type Code and Category are a handful of options the portal has published for
 * years, so they are `SelectField`s and the user picks. Buyer (about 130 names,
 * changing with staff) and NIGP Class (about 300 codes) are text: the scraper
 * matches what is typed against each option's value *and* its visible text, so
 * "Bell, Carla", "C.BELL" and "carla" all reach the same buyer — and a list
 * copied out of the portal today cannot go stale here tomorrow.
 */

/** Status, as the portal's Status dropdown publishes it. */
const STATUS_OPTIONS = [
  { value: "2BS", label: "Sent" },
  { value: "2BO", label: "Opened" },
  { value: "2BA", label: "Approved" },
  { value: "2BE", label: "Evaluated" },
  { value: "2BIA", label: "Intent To Award" },
  { value: "2BPO", label: "Bid to PO" },
  { value: "2BCL", label: "Closed" },
];

/** Type Code — the kind of buy. The portal's own labels, verbatim. */
const TYPE_CODE_OPTIONS = [
  { value: "IB", label: "Invitation and Bid, Citywide" },
  { value: "DB", label: "Invitation and Bid, Departmental" },
  { value: "WB", label: "Invitation and Bid, Public Works" },
  { value: "IC", label: "Invitation and Bid - Concession" },
  { value: "RQ", label: "Request for Proposal" },
  { value: "RI", label: "Request for Information" },
  { value: "MI", label: "Micro Purchase" },
  { value: "SO", label: "Small Order Purchase" },
  { value: "LB", label: "Local Business Purchase" },
  { value: "MP", label: "Miscellaneous PO for Prof Svcs 34K and Under" },
  { value: "BB", label: "Best Value, SSE" },
  { value: "BP", label: "Best Value, Public Works" },
  { value: "PQ", label: "Prequalification for Bidding" },
  { value: "EP", label: "Emergency Bid, Public Works" },
  { value: "ES", label: "Emergency Bid, SS&E" },
  { value: "SW", label: "Software Program Bid" },
  { value: "OA", label: "Office Automation Bid" },
  { value: "SU", label: "Surveillance, Security, and Fire Systems" },
  { value: "DP", label: "Demolition Program" },
  { value: "LD", label: "Large Demolition Contract" },
  { value: "SC", label: "Scrap Bid" },
];

/** Bids in Category — the portal's thirty-one commodity groupings. */
const CATEGORY_OPTIONS = [
  { value: "01", label: "Administrative, Financial, and Management Services" },
  { value: "02", label: "Agricultural Equipment and Related Products and Services" },
  { value: "03", label: "Arts, Crafts, Entertainment, Theatre" },
  { value: "04", label: "Automotive Products, Vehicles, and Services" },
  { value: "05", label: "Building Equipment, Supplies, and Services" },
  { value: "06", label: "Clothing, Textiles, Laundry Equipment, and Supplies" },
  { value: "07", label: "Communication Equipment and Services" },
  { value: "08", label: "Computers, Software, Supplies, and Services" },
  { value: "09", label: "Food, Equipment, and Related Services" },
  { value: "10", label: "Furnishings and Related Services" },
  { value: "11", label: "Furniture and Related Services" },
  { value: "12", label: "Hardware, Related Equipment, and Services" },
  { value: "13", label: "Highway Road Equipment, Materials, and Related Equipment" },
  { value: "14", label: "Janitorial and Cleaning Equipment, Supplies, and Services" },
  { value: "15", label: "Laboratory Equipment, Supplies, and Services" },
  { value: "16", label: "Maintenance and Repair of Equipment" },
  { value: "17", label: "Medical Equipment, Supplies, and Services" },
  { value: "18", label: "Miscellaneous Commodities and Services" },
  { value: "19", label: "Office Supplies, Related Items, and Services" },
  { value: "20", label: "Paper, Printing Equipment, and Related Products and Services" },
  { value: "21", label: "Personal Products, Equipment, and Services" },
  { value: "22", label: "Public Works, Park Equipment, and Construction Services" },
  { value: "23", label: "Rental and Leasing Services" },
  { value: "24", label: "Safety and Protection Equipment and Related Services" },
  { value: "25", label: "School and Library Equipment, Supplies, and Services" },
  { value: "26", label: "Sporting, Athletic, and other Outdoor Equipment and Services" },
  { value: "27", label: "Testing and Sampling Equipment and Services" },
  { value: "28", label: "The Trades: Electrical, Engineering, HVAC, Plumbing, and Welding" },
  { value: "29", label: "Transit Equipment and Related Services, Mass" },
  { value: "30", label: "Water and Sewer Treatment Equipment, Supplies, and Services" },
  { value: "31", label: "Environmentally Certified Products" },
];

export default function PhiladelphiaSearch({
  filters,
  onChange,
  disabled,
}: {
  filters: PhiladelphiaFilters;
  onChange: (next: PhiladelphiaFilters) => void;
  disabled?: boolean;
}) {
  const set = (key: keyof PhiladelphiaFilters) => (value: string) =>
    onChange({ ...filters, [key]: value });

  return (
    <Card
      title="Advanced Search"
      description="The portal's own search form. Fill in what you want to narrow by and leave the rest blank — a blank field is not a filter."
    >
      <div className="space-y-5">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field
            label="Description"
            hint="Words in the bid's title"
            value={filters.description ?? ""}
            onChange={set("description")}
            disabled={disabled}
            placeholder="e.g. pump replacement"
          />
          <Field
            label="Item description"
            hint="Words in a line item, not the title"
            value={filters.item_description ?? ""}
            onChange={set("item_description")}
            disabled={disabled}
            placeholder="e.g. submersible pump"
          />
          <Field
            label="Bid solicitation #"
            value={filters.bid_number ?? ""}
            onChange={set("bid_number")}
            disabled={disabled}
            placeholder="e.g. B2727750"
          />
          <Field
            label="Buyer"
            hint="Name or portal code — either matches"
            value={filters.buyer ?? ""}
            onChange={set("buyer")}
            disabled={disabled}
            placeholder="e.g. Bell, Carla"
          />
          <Field
            label="Department"
            hint="Only applies with an organization set"
            value={filters.department ?? ""}
            onChange={set("department")}
            disabled={disabled}
            placeholder="e.g. Water"
          />
          <Field
            label="NIGP class"
            hint="Code or name, e.g. 720 or pumping"
            value={filters.nigp_class ?? ""}
            onChange={set("nigp_class")}
            disabled={disabled}
            placeholder="e.g. 720"
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <SelectField
            label="Status"
            value={filters.status ?? ""}
            options={STATUS_OPTIONS}
            onChange={set("status")}
            disabled={disabled}
            placeholder="Any status"
          />
          <SelectField
            label="Type code"
            value={filters.type_code ?? ""}
            options={TYPE_CODE_OPTIONS}
            onChange={set("type_code")}
            disabled={disabled}
            placeholder="Any type"
          />
          <SelectField
            label="Category"
            value={filters.category ?? ""}
            options={CATEGORY_OPTIONS}
            onChange={set("category")}
            disabled={disabled}
            placeholder="Any category"
          />
          <Field
            label="Opening date from"
            hint="MM/DD/YYYY, as the portal writes it"
            value={filters.opening_date_from ?? ""}
            onChange={set("opening_date_from")}
            disabled={disabled}
            placeholder="08/01/2026"
          />
          <Field
            label="Opening date to"
            hint="MM/DD/YYYY, as the portal writes it"
            value={filters.opening_date_to ?? ""}
            onChange={set("opening_date_to")}
            disabled={disabled}
            placeholder="09/30/2026"
          />
        </div>

        <label className="flex items-start gap-2.5 text-xs text-ink-600">
          <input
            type="checkbox"
            checked={filters.match_any ?? false}
            disabled={disabled}
            onChange={(e) => onChange({ ...filters, match_any: e.target.checked })}
            className="mt-0.5 h-3.5 w-3.5 rounded border-ink-300 text-gold-500 focus:ring-2 focus:ring-gold-400/30 disabled:cursor-not-allowed"
          />
          <span>
            <span className="font-semibold text-ink-700">Match any criterion</span> — return a
            bid that matches any one of the fields above. Off, a bid has to match all of them.
          </span>
        </label>
      </div>
    </Card>
  );
}
