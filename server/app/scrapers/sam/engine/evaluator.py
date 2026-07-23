"""
SAM.gov Bid Evaluator — NAICS-first deterministic engine.

Source of truth: SAM_Bid_Evaluation_Spec_v1.docx (audit of 529 live bids).

Decision algorithm (strict order — see spec §3):

  STEP 0  Kill-Word Sieve         → instant REJECT on dealbreaker keyword
  STEP 1  Requirement Type        → HARDWARE / MATERIAL vs SERVICE
                                     (NAICS code is the primary signal,
                                      title keywords confirm/override)
  STEP 2  If HARDWARE             → PURSUE (Rule A), STOP. No location check.
  STEP 3  If SERVICE: Rule B?     → REJECT (excluded service, any location)
  STEP 4  If not Rule B: Rule C?  → proceed to location check
  STEP 5  Rule C service location → US Mainland = PURSUE, else REJECT
          Service on neither list → US Mainland = MANUAL_REVIEW, else REJECT

The cardinal rule (spec §1.2): hardware is classified BEFORE any Rule B/C or
location logic, and hardware is ALWAYS pursued regardless of delivery location.
"""

import logging
import re

logger = logging.getLogger(__name__)


# ===========================================================================
# Standard reason phrases (spec §7) — the ONLY phrases the reason field may use
# ===========================================================================

def reason_hardware() -> str:
    return "Hardware/material requirement — pursued regardless of location (Rule A)"

def reason_rule_c_pursue(num: int, name: str) -> str:
    return f"Allowed service (Rule C #{num} — {name}) + US Mainland place of performance"

def reason_rule_b(num: int, name: str) -> str:
    return f"Excluded service category (Rule B #{num} — {name}) — rejected regardless of location"

def reason_rule_c_outside() -> str:
    return "Allowed service (Rule C) but performed outside US Mainland"

def reason_not_listed_outside() -> str:
    return "Service not in allowed/excluded list + performed outside US Mainland"

def reason_not_listed_manual() -> str:
    return "Service not in allowed or excluded list — manual review required (US Mainland location)"


# ===========================================================================
# Rule B (excluded) and Rule C (allowed) category names (spec §2.2 / §2.3)
# ===========================================================================

RULE_B = {
    1:  "Maintenance, Repair and Inspection Services",
    2:  "Management Services",
    3:  "Management Software",
    4:  "Audit",
    5:  "Construction & Demolition Services",
    6:  "Rental of Equipment",
    7:  "Waste Management Services",
    8:  "Promotional Services",
    9:  "Training Services",
    10: "Custodial Services",
    11: "Lease of Equipment",
    12: "Engineering Support Services",
    13: "Hotel Room Booking and Lodging",
    14: "Yellow Ribbon",
    15: "Food Items",
    16: "Religious & Education Coordinator",
    17: "Real Estate",
    18: "Aircraft Lavatory Services",
    19: "Marine Vessel Upgrade",
    20: "Research & Development",
}

RULE_C = {
    1:  "Cable Installation",
    2:  "Fence Installation",
    3:  "Furniture Installation",
    4:  "UPS / Generator Repair and Maintenance",
    5:  "IT Hardware / Software Installation and Maintenance",
    6:  "HVAC Installation, Repair and Maintenance",
    7:  "Industrial Hardware Installation",
    8:  "Roofing Installation, Repair and Maintenance",
    9:  "Door / Window Installation",
    10: "AV Equipment Installation",
    11: "Storage Rack and Shelving Installation",
}


# ===========================================================================
# Location detection — US Mainland = contiguous 48 states only (spec §6.4)
# Alaska & Hawaii are OUTSIDE US Mainland for SERVICE bids.
# ===========================================================================

# Contiguous 48 states + DC (full names)
_CONTIGUOUS_NAMES = {
    "alabama", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts",
    "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska",
    "nevada", "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming",
    "district of columbia", "washington dc", "washington, dc",
}

# Contiguous 48 + DC postal abbreviations (matched case-sensitively on title)
_CONTIGUOUS_ABBR = {
    "AL", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}

# Non-mainland US (Alaska, Hawaii, territories) — OUTSIDE for services
_NON_MAINLAND = [
    "alaska", "hawaii", "guam", "puerto rico", "us virgin islands",
    "u.s. virgin islands", "virgin islands", "american samoa",
    "northern mariana islands", "northern mariana",
]
_NON_MAINLAND_ABBR = {"AK", "HI", "GU", "PR", "VI", "AS", "MP"}

# Fix 3: known overseas naval / military base names. For Rule C service bids the
# place of performance may be stated only in the description body (not the
# title), so these are matched against the full description text with word
# boundaries. Any hit flags the bid as outside US Mainland (e.g. MCM-14 Sasebo).
_OVERSEAS_BASES = [
    "sasebo", "yokosuka", "yokota", "rota", "bahrain", "manama", "singapore",
    "guam", "okinawa", "kadena", "osan", "ramstein", "aviano", "sigonella",
    "souda bay", "diego garcia", "camp humphreys",
]

# Foreign-location indicators
_FOREIGN = [
    "germany", "japan", "djibouti", "bermuda", "italy", "korea", "afghanistan",
    "iraq", "kuwait", "qatar", "bahrain", "united kingdom", "england", "spain",
    "poland", "belgium", "netherlands", "turkey ", "greece", "australia",
    "philippines", "singapore", "thailand", "kenya", "egypt", "jordan",
    "luanda", "angola", "diego garcia", "okinawa", "oconus", "overseas",
    "outside the united states", "outside the continental united states",
]


def _detect_location(title: str, hay: str, body: str = "") -> str:
    """
    Return "US_MAINLAND" or "OUTSIDE_MAINLAND".

    Priority: an explicit non-mainland / foreign indicator wins. Otherwise a
    contiguous-state name or abbreviation marks US Mainland. If nothing is
    found, default to US_MAINLAND (most SAM bids are domestic; the spec only
    rejects services on an *affirmative* outside-mainland finding).
    """
    # 1) Affirmative outside-mainland signals (highest priority)
    for kw in _NON_MAINLAND:
        if kw in hay:
            return "OUTSIDE_MAINLAND"
    for kw in _FOREIGN:
        if kw in hay:
            return "OUTSIDE_MAINLAND"
    # Fix 3: known overseas base names in the description body (place of
    # performance is often stated only in the body, not the title).
    body_l = (body or "").lower()
    for base in _OVERSEAS_BASES:
        if re.search(rf"\b{re.escape(base)}\b", body_l):
            return "OUTSIDE_MAINLAND"
    # Postal abbreviations for AK/HI/territories (case-sensitive, word-boundary)
    for abbr in _NON_MAINLAND_ABBR:
        if re.search(rf"\b{abbr}\b", title):
            return "OUTSIDE_MAINLAND"

    # 2) Contiguous-state full names
    for name in _CONTIGUOUS_NAMES:
        if name in hay:
            return "US_MAINLAND"
    # 3) Contiguous-state postal abbreviations (case-sensitive on title)
    for abbr in _CONTIGUOUS_ABBR:
        if re.search(rf"\b{abbr}\b", title):
            return "US_MAINLAND"

    # 4) Default — assume domestic US Mainland
    return "US_MAINLAND"


# ===========================================================================
# Keyword helpers
# ===========================================================================

def _has(hay: str, *keywords: str) -> bool:
    return any(k in hay for k in keywords)

_INSTALL_ACTIONS = (
    "install", "installation", "replace", "replacement", "upgrade", "setup",
    "set up", "erection", "erect", "pull", "run ", "wiring", "construct ",
    "new ",
)
_SERVICE_VERBS = (
    "maintenance", "repair", "overhaul", "inspection", "inspect", "servicing",
    "preventive maintenance", "pm service",
)


# ===========================================================================
# NAICS parsing
# ===========================================================================

def _naics_prefix(naics_code: str) -> int | None:
    """Return the first 3 digits of the NAICS code as an int, or None."""
    if not naics_code:
        return None
    digits = re.sub(r"\D", "", str(naics_code))
    if len(digits) >= 3:
        return int(digits[:3])
    return None

def _naics_full(naics_code: str) -> str:
    return re.sub(r"\D", "", str(naics_code or ""))


def _is_manufacturing(prefix: int | None) -> bool:
    # 311–339 manufacturing, plus 423–424 durable/non-durable wholesale
    if prefix is None:
        return False
    return (311 <= prefix <= 339) or (423 <= prefix <= 424) or (420 <= prefix <= 429)


# ===========================================================================
# Rule B / Rule C classifiers
# ===========================================================================

def _check_rd(hay: str, naics_full: str) -> bool:
    """Rule B #20 — Research & Development (spec §6.8)."""
    if re.search(r"\bbaa\b", hay):
        return True
    if _has(hay, "broad agency announcement", "nextstep", "next step",
            "sbir", "sttr", "research and development", "r&d", "r & d"):
        return True
    # R&D NAICS (541713/541714/541715 — research & development)
    if naics_full[:6] in {"541713", "541714", "541715"}:
        return True
    return False


def _check_marine_vessel(hay: str, naics_full: str) -> bool:
    """Rule B #19 — Marine Vessel Upgrade / Refit (spec §2.2, §6.2)."""
    if _has(hay, "drydock", "dry dock", "dry-dock", "dockside", "ssra",
            "vessel overhaul", "ship refit", "vessel refit", "hull ",
            "vessel modification", "repower", "vessel repair"):
        return True
    if re.search(r"\bmta\b", hay) and naics_full.startswith("336611"):
        return True
    # 336611 ship building/repairing with a vessel-SERVICE title (spec §6.2).
    # NOTE: bare "repair" is intentionally excluded — "repair parts" is a
    # hardware/spare-parts supply, not a vessel service. Use vessel-specific
    # service phrases only.
    if naics_full.startswith("336611") and _has(
        hay, "open and inspect", "open/inspect", "dockside repair", "renewal",
        "overhaul", "drydock", "ssra", "refit",
    ):
        return True
    return False


def _check_rule_c(hay: str) -> tuple[int, str] | None:
    """
    Return (category_number, category_name) if the bid is a Rule C allowed
    service, else None. Each matcher requires BOTH an equipment keyword AND an
    action keyword so that physical-product titles (e.g. "HVAC Controller
    circuit card") are NOT misclassified as services.
    """
    install = _has(hay, *_INSTALL_ACTIONS)
    serviceable = install or _has(hay, *_SERVICE_VERBS)

    # #2 Fence Installation — only installation, NOT removal/demolition (§6.3)
    if _has(hay, "fence", "fencing", "perimeter fenc"):
        if _has(hay, "removal", "remove", "demolition", "demolish", "tear down"):
            return None  # falls through to Rule B #5 (demolition)
        if install:
            return (2, RULE_C[2])

    # #4 UPS / Generator Repair and Maintenance
    if _has(hay, "generator", "genset", "ups ", "uninterruptible power") and serviceable:
        return (4, RULE_C[4])

    # #6 HVAC Installation, Repair and Maintenance
    if _has(hay, "hvac", "a/c ", "air conditioning", "air-conditioning",
            "chiller", "cooling coil", "heater", "heating") and serviceable:
        return (6, RULE_C[6])

    # #8 Roofing Installation, Repair and Maintenance
    if _has(hay, "roof", "roofing") and serviceable:
        return (8, RULE_C[8])

    # #9 Door / Window Installation
    if _has(hay, "window", "door", "glazing") and serviceable:
        return (9, RULE_C[9])

    # #1 Cable Installation
    if _has(hay, "cable", "fiber optic", "fiber-optic", "foc ", "network cable",
            "efi&t", "structured cabling") and (install or _has(hay, "pull", "run")):
        return (1, RULE_C[1])

    # #10 AV Equipment Installation
    if _has(hay, "audio/visual", "audio visual", "av upgrade", "av equipment",
            "pa system", "projector", "vtc", "video teleconfer", "display install") and serviceable:
        return (10, RULE_C[10])

    # #3 Furniture Installation
    if _has(hay, "furniture") and install:
        return (3, RULE_C[3])

    # #11 Storage Rack and Shelving Installation
    if _has(hay, "pallet rack", "shelving", "storage rack", "storage system",
            "racking") and install:
        return (11, RULE_C[11])

    # #5 IT Hardware / Software Installation and Maintenance
    if _has(hay, "server rack", "network device", "it equipment", "it hardware",
            "network switch install") and serviceable:
        return (5, RULE_C[5])

    # #7 Industrial Hardware Installation
    if _has(hay, "machinery install", "equipment installation",
            "tank installation", "industrial hardware") and install:
        return (7, RULE_C[7])

    return None


# --- Fix 2: consumable-food words for the NAICS 311/312 hardware sub-check ---
_FOOD_PRODUCT_WORDS = (
    r"milk", r"meats?", r"poultry", r"produce", r"subsistence", r"food items?",
)


def _title_is_food_item(hay: str) -> bool:
    """True if the title names a consumable food product (Fix 2). Used only
    inside the hardware gate for food-manufacturing NAICS (311/312)."""
    return any(re.search(rf"\b{t}\b", hay) for t in _FOOD_PRODUCT_WORDS)


def _check_food(hay: str) -> bool:
    """
    Rule B #15 — Food Items. Matches actual food/consumables ONLY.
    Excludes apparel, equipment, packaging, and fuel (spec §6.5, §9.2).
    """
    # Whole-word food terms (word boundaries prevent matches like
    # "respiRATION" / "filtRATION" / "OILseed").
    food_terms = (
        r"subsistence", r"poultry", r"turkey", r"meat", r"beef", r"pork",
        r"produce", r"provisions", r"food items?", r"foodstuffs?",
        r"fresh fruit", r"vegetables?", r"dairy", r"meals?", r"rations?",
    )
    food_present = any(re.search(rf"\b{t}\b", hay) for t in food_terms)
    if not food_present:
        return False

    # Food-adjacent hardware (apparel, fuel, packaging, equipment) is NOT a
    # food item unless an explicit raw-food term is present (spec §6.5/§9.2).
    if _has(hay, "smock", "jacket", "apparel", "uniform", "clothing",
            "propane", "fuel", "petroleum", "packaging", "equipment"):
        raw_food = any(
            re.search(rf"\b{t}\b", hay)
            for t in (r"subsistence", r"poultry", r"turkey", r"produce",
                      r"fresh fruit", r"vegetables?", r"meat", r"rations?")
        )
        if not raw_food:
            return False
    return True


def _check_rule_b(hay: str) -> tuple[int, str] | None:
    """
    Return (category_number, category_name) for the matching Rule B excluded
    service, else None. Checked AFTER Rule C so that allowed maintenance
    services (generator, HVAC, roofing, IT) are not swallowed by Rule B #1.
    """
    # #20 R&D and #19 Marine vessel are checked by callers earlier.

    # #5 Construction & Demolition (incl. removal/demolition as primary)
    if _has(hay, "construction contract", "demolition", "demolish",
            "excavat", "grading", "site prep", "substation",
            "infrastructure build"):
        return (5, RULE_B[5])
    if _has(hay, "fence", "fencing") and _has(hay, "removal", "remove",
            "demolition", "demolish"):
        return (5, RULE_B[5])
    if re.search(r"\bconstruct\b", hay) or re.search(r"\bconstruction\b", hay):
        # generic construction (build) — but not "construction-grade" products
        if _has(hay, "build", "erect building", "new building", "site work"):
            return (5, RULE_B[5])

    # #7 Waste Management
    if _has(hay, "hazardous waste", "solid waste", "waste management",
            "waste collection", "waste disposal", "recycling", "refuse"):
        return (7, RULE_B[7])

    # #11 Lease of Equipment / #6 Rental
    if _has(hay, "lease", "leasing"):
        return (11, RULE_B[11])
    if _has(hay, "rental", "rent of", "equipment rental"):
        return (6, RULE_B[6])

    # #9 Training Services
    if _has(hay, "flight training", "operator training", "training course",
            "courseware", "curriculum", "instruction"):
        return (9, RULE_B[9])
    if _has(hay, "training") and not _has(hay, "at delivery", "operator training at"):
        return (9, RULE_B[9])

    # #3 Management Software
    if _has(hay, "software license", "license renewal", "software support",
            "software maintenance", "saas", "erp", "subscription renewal",
            "software subscription"):
        return (3, RULE_B[3])

    # #2 Management Services
    if _has(hay, "program management", "project management", "management services",
            "advisory services", "it management", "cybersecurity services"):
        return (2, RULE_B[2])

    # #4 Audit
    if _has(hay, "financial audit", "it audit", "compliance review", "audit "):
        return (4, RULE_B[4])

    # #10 Custodial Services
    if _has(hay, "janitorial", "custodial", "carpet cleaning", "duct cleaning"):
        return (10, RULE_B[10])
    if _has(hay, "cleaning service", "cleaning services"):
        return (10, RULE_B[10])

    # #12 Engineering Support Services
    if _has(hay, "engineering support", "engineering advisory", "design support"):
        return (12, RULE_B[12])

    # #13 Hotel / Lodging
    if _has(hay, "hotel", "lodging", "accommodation", "conference room"):
        return (13, RULE_B[13])

    # #14 Yellow Ribbon
    if _has(hay, "yellow ribbon"):
        return (14, RULE_B[14])

    # #16 Religious & Education Coordinator
    if _has(hay, "chaplain", "religious coordinator", "religious education",
            "education coordinator", "religious"):
        return (16, RULE_B[16])

    # #17 Real Estate
    if _has(hay, "real estate", "property lease", "land acquisition",
            "facility lease"):
        return (17, RULE_B[17])

    # #18 Aircraft Lavatory
    if _has(hay, "lavatory"):
        return (18, RULE_B[18])

    # #8 Promotional Services
    if _has(hay, "promotional", "advertising", "marketing services"):
        return (8, RULE_B[8])

    # #15 Food Items
    if _check_food(hay):
        return (15, RULE_B[15])

    # #1 Maintenance, Repair and Inspection (general — last, catch-all)
    if _has(hay, *_SERVICE_VERBS) or _has(hay, "open and inspect", "open/inspect"):
        return (1, RULE_B[1])

    return None


# ===========================================================================
# Step 1 — Hardware vs Service classification (spec §3 Step 1, §4)
# ===========================================================================

# Strong product/supply signals. Per the spec override rule (§3 Step 1, Table 8),
# a manufacturing-NAICS bid whose title carries one of these is HARDWARE even if
# it also contains a service verb — e.g. "repair parts", "spare parts", "repair
# kit" are spare-parts SUPPLIES, not repair services.
_PRODUCT_SIGNALS = (
    "part number", "p/n", "nsn", "national stock number", "spare part",
    "spare parts", "repair parts", "repair kit", "parts kit", "parts",
    "spares", "kit", "assortment", "qty", "quantity", "supplies", "supply of",
    "procurement of", "purchase of", "rfq", "components",
)

_HARDWARE_TITLE_SIGNALS = (
    "purchase", "supply of", "supply", "procurement", "procure", "spare parts",
    "spare part", "part number", "p/n", "nsn", "quantity", "qty", "buckle",
    "forklift", "vehicle", "engine", "turbosupercharger", "valve", "gasket",
    "kit", "assembly", "amplifier", "detector", "printer", "filtration",
    "mattress", "simulator", "laryngoscope", "microscanner", "switches",
    "teslameter", "composter", "tools", "barrels", "components", "device",
)


def _has_product_signal(hay: str) -> bool:
    """Whole-word match for strong product/supply signals (avoids 'parts' inside
    other words and 'rfq' false hits)."""
    for sig in _PRODUCT_SIGNALS:
        if re.search(rf"(?<!\w){re.escape(sig)}(?!\w)", hay):
            return True
    return False


# --- Fix 1: service-title override (spec §3 Step 1) ------------------------
# A leading service verb in the title, or an explicit "for services" /
# "services contract" phrase in the description opening, marks the bid as a
# SERVICE even when PN/QTY/NSN product signals are present. This corrects
# hardware-shaped titles that are really repair/overhaul contracts (e.g. the
# FMS Repair and USS Isaac Mayo Awning cases).
_SERVICE_TITLE_VERBS = {
    "repair", "repairs", "repairing",
    "overhaul", "overhauls", "overhauling",
    "inspect", "inspection", "inspections", "inspecting",
    "calibrate", "calibration", "calibrations", "calibrating",
}
# A service verb immediately followed by one of these nouns is a hardware
# SUPPLY ("repair parts", "repair kit", "spare parts") — NOT a service.
_HARDWARE_NOUN_AFTER = {
    "part", "parts", "kit", "kits", "spare", "spares",
    "assortment", "assortments",
}


def _service_verb_leads_title(hay: str) -> bool:
    """True if the title's first meaningful verb is Repair/Overhaul/Inspect/
    Calibrate — but NOT when it forms a hardware noun phrase ('repair parts')."""
    tokens = re.findall(r"[a-z]+", hay)
    for i, tok in enumerate(tokens):
        if tok in _SERVICE_TITLE_VERBS:
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            return nxt not in _HARDWARE_NOUN_AFTER
    return False


def _service_title_override(hay: str, full_text: str) -> bool:
    """Fix 1 — leading service verb OR a 'for services'/'services contract'
    phrase in the description's opening 200 characters."""
    if _service_verb_leads_title(hay):
        return True
    opening = (full_text or "")[:200].lower()
    if "for services" in opening or "services contract" in opening:
        return True
    return False


def _classify_requirement(hay: str, naics_code: str, full_text: str = "") -> str:
    """
    Return "HARDWARE" or "SERVICE".

    NAICS is the primary signal (spec §4). For manufacturing/wholesale codes a
    bid is HARDWARE unless its title carries a decisive service signal (e.g.
    "generator maintenance"). For construction/service codes the bid is a
    SERVICE. With no NAICS, title hardware-signals decide.
    """
    prefix = _naics_prefix(naics_code)

    # Construction (236–238) → always SERVICE (Rule C candidate)
    if prefix is not None and 236 <= prefix <= 238:
        return "SERVICE"

    # Manufacturing (311–339) / Wholesale (42x):
    # Override rule (spec §3 Step 1) — a product/supply title is HARDWARE even if
    # it contains service words ("repair parts", "spare parts kit", NSN, qty…).
    if _is_manufacturing(prefix):
        # Fix 1: a leading service verb (Repair/Overhaul/Inspect/Calibrate) or an
        # explicit "for services"/"services contract" description opening wins
        # over PN/QTY product signals — this is a service, not a supply.
        if _service_title_override(hay, full_text):
            return "SERVICE"
        if _has_product_signal(hay):
            return "HARDWARE"
        if _service_signal_present(hay):
            return "SERVICE"
        return "HARDWARE"

    # Known service NAICS ranges → SERVICE
    if prefix is not None and (
        prefix in (115,) or 481 <= prefix <= 928
    ):
        return "SERVICE"

    # No / unknown NAICS — decide by title content.
    if _service_signal_present(hay):
        return "SERVICE"
    if _has(hay, *_HARDWARE_TITLE_SIGNALS):
        return "HARDWARE"
    # Ambiguous with no NAICS and no signals — treat as service (will route to
    # manual review / location logic rather than auto-pursue).
    return "SERVICE"


def _service_signal_present(hay: str) -> bool:
    """
    True if the text carries a decisive SERVICE signal that should override a
    manufacturing-NAICS hardware default: a Rule C equipment+action match, a
    general maintenance/repair/overhaul/inspection verb, or vessel/drydock work.
    """
    if _has(hay, *_SERVICE_VERBS):
        return True
    if _has(hay, "drydock", "dry dock", "dockside", "ssra", "vessel"):
        return True
    if _check_rule_c(hay) is not None:
        return True
    return False


# ===========================================================================
# Public API
# ===========================================================================

def evaluate_bid(
    bid_id: str,
    full_text: str,
    config: dict,
    naics_code: str = "",
    title: str = "",
) -> dict:
    """
    Evaluate a bid per SAM_Bid_Evaluation_Spec_v1.

    Parameters
    ----------
    bid_id     : Unique bid identifier (passed through to the result).
    full_text  : Combined description + document text.
    config     : The ``sam`` section of config.yml (must contain ``evaluation``).
    naics_code : NAICS code string (primary hardware/service signal).
    title      : Notice title (most reliable categorisation signal).

    Returns
    -------
    dict with keys: bid_id, decision, reason, requirement_type, rule,
    location, stopped_at_step.

    decision ∈ {PURSUE, REJECT, MANUAL_REVIEW}
    The ``reason`` field always uses one of the six standard phrases (spec §7).
    """
    eval_cfg   = config.get("evaluation", {})
    kill_words = [w.lower() for w in eval_cfg.get("kill_words", [])]

    # Classification is TITLE-PRIMARY (spec §3 Step 1 + override rule §1.2): the
    # notice title states the primary requirement. The full document body is a
    # 120K-char dump of FAR boilerplate (which mentions inspection, training,
    # audit, food, R&D, etc. in standard clauses) and must NOT drive Rule B/C
    # matching — doing so falsely re-classifies hardware bids. Body text is used
    # only as a fallback when the title is empty.
    classify_text = title.strip() if (title and title.strip()) else full_text[:2000]
    hay = classify_text.lower()

    result = {
        "bid_id":           bid_id,
        "decision":         None,
        "reason":           "",
        "requirement_type": None,
        "rule":             None,
        "location":         None,
        "stopped_at_step":  None,
    }

    # ── STEP 0: Kill-Word Sieve ──────────────────────────────────────────────
    for word in kill_words:
        if word and word in hay:
            result.update(
                decision="REJECT",
                stopped_at_step=0,
                rule="kill_word",
                reason=f"Contains dealbreaker keyword: {word}",
            )
            logger.info(f"[EVAL] {bid_id} -> REJECT @ kill-word ({word})")
            return result

    naics_full = _naics_full(naics_code)

    # ── R&D override (Rule B #20) — applies regardless of NAICS (spec §6.8/§9.5)
    if _check_rd(hay, naics_full):
        result.update(
            decision="REJECT", stopped_at_step=3, rule="B20",
            requirement_type="SERVICE", reason=reason_rule_b(20, RULE_B[20]),
        )
        logger.info(f"[EVAL] {bid_id} -> REJECT @ Rule B #20 (R&D)")
        return result

    # ── Marine vessel service (Rule B #19) — before hardware (336611 dual-use)
    if _check_marine_vessel(hay, naics_full):
        result.update(
            decision="REJECT", stopped_at_step=3, rule="B19",
            requirement_type="SERVICE", reason=reason_rule_b(19, RULE_B[19]),
        )
        logger.info(f"[EVAL] {bid_id} -> REJECT @ Rule B #19 (marine vessel)")
        return result

    # ── STEP 1: Hardware vs Service ──────────────────────────────────────────
    req_type = _classify_requirement(hay, naics_code, full_text)
    result["requirement_type"] = req_type

    # ── STEP 2: Hardware → PURSUE (Rule A), STOP ─────────────────────────────
    if req_type == "HARDWARE":
        # Fix 2: food-manufacturing NAICS (311/312) sub-check — a consumable
        # food item is Rule B #15 REJECT even though NAICS is 311–339 hardware.
        if _naics_prefix(naics_code) in (311, 312) and _title_is_food_item(hay):
            result.update(
                decision="REJECT", stopped_at_step=3, rule="B15",
                requirement_type="SERVICE", reason=reason_rule_b(15, RULE_B[15]),
            )
            logger.info(f"[EVAL] {bid_id} -> REJECT @ Rule B #15 (food NAICS 311/312)")
            return result
        # Food items are the one manufactured-product exception (Rule B #15).
        if _check_food(hay):
            result.update(
                decision="REJECT", stopped_at_step=3, rule="B15",
                requirement_type="SERVICE", reason=reason_rule_b(15, RULE_B[15]),
            )
            logger.info(f"[EVAL] {bid_id} -> REJECT @ Rule B #15 (food)")
            return result
        result.update(
            decision="PURSUE", stopped_at_step=2, rule="A",
            reason=reason_hardware(),
        )
        logger.info(f"[EVAL] {bid_id} -> PURSUE @ Rule A (hardware)")
        return result

    # ── STEP 3: Service → Rule B (excluded) check ────────────────────────────
    rule_c = _check_rule_c(hay)
    if rule_c is None:
        rule_b = _check_rule_b(hay)
        if rule_b is not None:
            num, name = rule_b
            result.update(
                decision="REJECT", stopped_at_step=3, rule=f"B{num}",
                reason=reason_rule_b(num, name),
            )
            logger.info(f"[EVAL] {bid_id} -> REJECT @ Rule B #{num}")
            return result

    # ── STEP 4: Rule C (allowed) check ───────────────────────────────────────
    location = _detect_location(classify_text, hay, body=full_text)
    result["location"] = location

    if rule_c is not None:
        num, name = rule_c
        # ── STEP 5: location gate for Rule C services ────────────────────────
        if location == "US_MAINLAND":
            result.update(
                decision="PURSUE", stopped_at_step=5, rule=f"C{num}",
                reason=reason_rule_c_pursue(num, name),
            )
            logger.info(f"[EVAL] {bid_id} -> PURSUE @ Rule C #{num} (US Mainland)")
        else:
            result.update(
                decision="REJECT", stopped_at_step=5, rule=f"C{num}",
                reason=reason_rule_c_outside(),
            )
            logger.info(f"[EVAL] {bid_id} -> REJECT @ Rule C #{num} (outside US Mainland)")
        return result

    # ── Service on neither list ──────────────────────────────────────────────
    if location == "US_MAINLAND":
        result.update(
            decision="MANUAL_REVIEW", stopped_at_step=4, rule="none",
            reason=reason_not_listed_manual(),
        )
        logger.info(f"[EVAL] {bid_id} -> MANUAL_REVIEW (not listed, US Mainland)")
    else:
        result.update(
            decision="REJECT", stopped_at_step=4, rule="none",
            reason=reason_not_listed_outside(),
        )
        logger.info(f"[EVAL] {bid_id} -> REJECT (not listed, outside US Mainland)")
    return result
