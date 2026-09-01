"""
SAM.gov Bid Evaluator — requirement-type-first deterministic engine.

Source of truth: evaluation_criteria_sam_bids.docx — the company's accepted
"Official PURSUE / REJECT Decision Guide (Company Scope of Work)". This engine
implements that guide's Combined Decision Matrix (§3) exactly:

  Requirement type            | US Mainland   | Outside US Mainland
  ----------------------------|---------------|--------------------
  Hardware / material supply  | PURSUE        | PURSUE
  Allowed service (Rule C)    | PURSUE        | REJECT
  Excluded service (Rule B)   | REJECT        | REJECT
  Service on neither list     | MANUAL_REVIEW | REJECT

Three decision modes — PURSUE, REJECT, or MANUAL_REVIEW. An unlisted service
(matching neither Rule B nor Rule C) performed in the US Mainland is not
auto-decided: it is flagged MANUAL_REVIEW so a human validates scope, rather
than being silently rejected. The same requirement performed OUTSIDE the US
Mainland is REJECTED outright (location alone disqualifies it). Hardware and
listed services (Rules A/B/C) are always auto-decided to PURSUE or REJECT.

Decision algorithm (strict order — the guide's Decision Flow §4):

  STEP 0  Kill-Word Sieve         → instant REJECT on dealbreaker keyword
                                     (operational pre-filter: rfi / sources
                                      sought / market research — these are not
                                      biddable solicitations. IDIQ is NOT a
                                      kill-word: hardware IDIQ contracts are
                                      biddable and must not be auto-rejected.)
  STEP 0b Rental override         → "rental" / "rental services" named as the
                                     primary scope → REJECT Rule B #6 outright,
                                     ahead of the hardware gate and Rule C.
  STEP 1  Requirement Type        → HARDWARE / MATERIAL vs SERVICE
                                     (NAICS code is the primary signal,
                                      title keywords confirm/override)
  STEP 2  If HARDWARE             → PURSUE (Rule A), STOP. No location check.
  STEP 3  If SERVICE: Rule B?     → REJECT (excluded service, any location)
  STEP 4  If not Rule B: Rule C?  → proceed to location check
  STEP 5  Rule C service location → US Mainland = PURSUE, else REJECT
          Service on neither list → US Mainland = MANUAL_REVIEW,
                                    outside US Mainland = REJECT

The cardinal rule (guide §1): the requirement type is classified BEFORE any
Rule B/C or location logic — decisions are driven primarily by WHAT is
procured, not WHERE — and hardware is ALWAYS pursued regardless of location.
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

def reason_not_listed_us() -> str:
    return "Service not in allowed (Rule C) or excluded (Rule B) list — not a validated in-scope service"

def reason_manual_review() -> str:
    return "Manual Review required — service not in Rule B or Rule C scope list"


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

# Foreign-country names (word-boundary matched). Comprehensive so that a place
# of performance stated as a country — "Jakarta, Indonesia", "Sierra Leone" —
# reads as OUTSIDE_MAINLAND. Deliberately EXCLUDES "georgia" (collides with the
# US state, handled as mainland) and bare "chad"/"jordan" are matched only with
# word boundaries. "united states"/"usa" are of course NOT here.
_FOREIGN_COUNTRIES = {
    # Americas (ex-US)
    "canada", "mexico", "guatemala", "honduras", "el salvador", "nicaragua",
    "costa rica", "panama", "colombia", "venezuela", "ecuador", "peru",
    "bolivia", "chile", "argentina", "brazil", "uruguay", "paraguay", "cuba",
    "haiti", "dominican republic", "jamaica", "bahamas", "belize", "suriname",
    "guyana", "trinidad",
    # Europe
    "united kingdom", "great britain", "england", "scotland", "wales",
    "ireland", "france", "germany", "italy", "spain", "portugal", "belgium",
    "netherlands", "holland", "luxembourg", "switzerland", "austria", "poland",
    "czech republic", "czechia", "slovakia", "hungary", "romania", "bulgaria",
    "greece", "cyprus", "malta", "ukraine", "russia", "belarus", "moldova",
    "sweden", "norway", "finland", "denmark", "iceland", "estonia", "latvia",
    "lithuania", "croatia", "serbia", "slovenia", "bosnia", "albania",
    "north macedonia", "montenegro", "kosovo",
    # Middle East
    "iraq", "iran", "syria", "yemen", "oman", "qatar", "kuwait", "bahrain",
    "saudi arabia", "united arab emirates", "u.a.e.", "israel", "jordan",
    "lebanon", "palestine",
    # Africa
    "egypt", "libya", "tunisia", "algeria", "morocco", "sudan", "south sudan",
    "ethiopia", "eritrea", "somalia", "kenya", "tanzania", "uganda", "rwanda",
    "burundi", "djibouti", "angola", "mozambique", "zambia", "zimbabwe",
    "malawi", "botswana", "namibia", "south africa", "lesotho", "eswatini",
    "madagascar", "mauritius", "senegal", "gambia", "guinea-bissau", "guinea",
    "sierra leone", "liberia", "ivory coast", "cote d'ivoire", "ghana", "togo",
    "benin", "nigeria", "niger", "mali", "burkina faso", "mauritania", "chad",
    "cameroon", "gabon", "congo", "central african republic",
    # Asia / Pacific
    "afghanistan", "pakistan", "india", "bangladesh", "sri lanka", "nepal",
    "bhutan", "maldives", "china", "taiwan", "hong kong", "mongolia", "japan",
    "south korea", "north korea", "korea", "vietnam", "cambodia", "laos",
    "thailand", "myanmar", "burma", "malaysia", "indonesia", "philippines",
    "singapore", "brunei", "timor-leste", "papua new guinea", "australia",
    "new zealand", "fiji", "kazakhstan", "uzbekistan", "turkmenistan",
    "kyrgyzstan", "tajikistan", "azerbaijan", "armenia", "turkey",
}

# Well-known foreign cities that host US diplomatic posts or bases and often
# appear WITHOUT their country in a place-of-performance line (word-boundary).
_FOREIGN_CITIES = {
    "jakarta", "manila", "seoul", "tokyo", "bangkok", "hanoi", "kabul",
    "baghdad", "manama", "doha", "riyadh", "dubai", "abu dhabi", "amman",
    "beirut", "cairo", "nairobi", "addis ababa", "kampala", "kinshasa",
    "lagos", "abuja", "accra", "dakar", "freetown", "monrovia", "luanda",
    "pretoria", "johannesburg", "islamabad", "new delhi", "dhaka", "colombo",
    "kuala lumpur", "beijing", "shanghai", "frankfurt", "berlin", "brussels",
    "the hague", "geneva", "vienna", "warsaw", "kyiv", "moscow", "ankara",
    "istanbul",
}

# Explicit "this bid is performed overseas" phrases (never in FAR boilerplate).
_OVERSEAS_PHRASES = [
    "oconus", "overseas", "outside the united states",
    "outside the continental united states", "outside conus",
]

# Diplomatic-post / foreign-base indicators. A US Embassy, Consulate, Naval
# Operating Base (NOB) etc. is by definition on foreign soil, so any of these
# anywhere in the text flags the bid OUTSIDE_MAINLAND. These strings do not
# appear in the FAR certification clauses that pollute the document body, so
# they are safe to scan against the whole body.
_DIPLOMATIC_RE = re.compile(
    r"\b(?:u\.?s\.?|american)\s+embassy\b"
    r"|\bembassy\s+of\s+the\s+united\s+states\b"
    r"|\bamerican\s+consulate\b"
    r"|\bconsulate\b|\bconsular\b|\bchancery\b|\bdiplomatic\s+post\b"
    r"|\bnob\s+[a-z]",  # "NOB <city>" — Naval Operating Base abroad
    re.IGNORECASE,
)

# Cues that mark where a place of performance is stated in the body — foreign
# country/city names are trusted only within ~120 chars after one of these, so
# a country named inside an unrelated FAR sanction clause does NOT flip the bid.
_POP_CUE_RE = re.compile(
    r"place\s+of\s+performance|performance\s+location|delivery\s+location|"
    r"ship[\s-]*to|pop\s*[:\-]|location\s*[:\-]|country\s*[:\-]|"
    r"performed\s+(?:at|in)",
    re.IGNORECASE,
)


def _foreign_token_present(text: str) -> bool:
    """True if any foreign country or well-known foreign city appears in `text`
    as a whole word (word boundaries stop 'india' matching 'indiana')."""
    for token in _FOREIGN_COUNTRIES:
        if re.search(rf"\b{re.escape(token)}\b", text):
            return True
    for city in _FOREIGN_CITIES:
        if re.search(rf"\b{re.escape(city)}\b", text):
            return True
    return False


def _body_place_is_foreign(body_l: str) -> bool:
    """Scan the description body for an overseas place of performance without
    tripping on FAR-clause country names. Country/city tokens count only inside
    the window that follows a place-of-performance cue; diplomatic-post and
    overseas-base/phrase indicators count anywhere (they never occur in FAR
    boilerplate)."""
    if not body_l:
        return False
    if _DIPLOMATIC_RE.search(body_l):
        return True
    for phrase in _OVERSEAS_PHRASES:
        if phrase in body_l:
            return True
    for base in _OVERSEAS_BASES:
        if re.search(rf"\b{re.escape(base)}\b", body_l):
            return True
    # Country/city names only inside a place-of-performance window.
    for cue in _POP_CUE_RE.finditer(body_l):
        window = body_l[cue.end(): cue.end() + 120]
        if _foreign_token_present(window):
            return True
    return False


def _detect_location(title: str, hay: str, body: str = "") -> str:
    """
    Return "US_MAINLAND" or "OUTSIDE_MAINLAND".

    Priority: an explicit non-mainland / foreign indicator wins. Otherwise a
    contiguous-state name or abbreviation marks US Mainland. If nothing is
    found, default to US_MAINLAND (most SAM bids are domestic; the spec only
    rejects services on an *affirmative* outside-mainland finding).
    """
    body_l = (body or "").lower()

    # 1) Affirmative outside-mainland signals (highest priority)
    for kw in _NON_MAINLAND:
        if kw in hay:
            return "OUTSIDE_MAINLAND"
    for phrase in _OVERSEAS_PHRASES:
        if phrase in hay:
            return "OUTSIDE_MAINLAND"
    # Title (reliable) is scanned against the full foreign country/city list and
    # the diplomatic-post patterns.
    if _foreign_token_present(hay) or _DIPLOMATIC_RE.search(hay):
        return "OUTSIDE_MAINLAND"
    # Body (noisy) — overseas bases, diplomatic posts, overseas phrases, and
    # country/city names gated to a place-of-performance cue (Fix 1).
    if _body_place_is_foreign(body_l):
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


def _is_rental_primary(hay: str, full_text: str = "") -> bool:
    """Fix 2 — True when "rental" / "rental services" is the primary scope.

    Whole-word match on the title-primary `hay`, or in the opening of the
    description body (rentals are stated up front, not buried in FAR
    boilerplate). Rented equipment is Rule B #6 regardless of what is rented, so
    this fires ahead of the hardware gate and Rule C matching."""
    if re.search(r"\brental\b", hay):
        return True
    opening = (full_text or "")[:500].lower()
    return bool(re.search(r"\brental\b", opening))


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


def _check_rule_c(hay: str, naics_full: str = "") -> tuple[int, str] | None:
    """
    Return (category_number, category_name) if the bid is a Rule C allowed
    service, else None. Each matcher requires BOTH an equipment keyword AND an
    action keyword so that physical-product titles (e.g. "HVAC Controller
    circuit card") are NOT misclassified as services.

    Fix 3 (re-run of the 121 catch-all bids): under a special-trade construction
    NAICS (238xxx) the requirement is inherently install/repair work, so the
    equipment keyword alone is decisive — no separate action verb is required:
        * 238xxx + HVAC/cooling/mini-split   → Rule C #6
        * 238210 + wiring/cable/conduit      → Rule C #1
        * 238990 + fence                     → Rule C #2
    """
    naics6 = (naics_full or "")[:6]
    naics3 = (naics_full or "")[:3]

    # -- NAICS-gated fast paths (equipment keyword alone is enough) ------------
    if naics3 == "238":
        if _has(hay, "hvac", "mini-split", "mini split", "minisplit", "cooling",
                "air conditioning", "air-conditioning", "chiller", "condenser",
                "heating", "heater"):
            return (6, RULE_C[6])
        if naics6 == "238210" and _has(hay, "wiring", "cable", "cabling",
                                       "conduit", "electrical"):
            return (1, RULE_C[1])
        if naics6 == "238990" and _has(hay, "fence", "fencing"):
            return (2, RULE_C[2])

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
# Consulted ONLY for a bid whose NAICS is already food manufacturing (311/312),
# which is why it can afford to be broad: the NAICS has established that the
# buyer is procuring food, and this only has to recognise which food. A term
# here cannot affect a bid under any other NAICS.
#
# It started at six terms, which left a food buyer's catalogue deciding by
# whichever item a line happened to name: one agency's "NATIONAL MENU - TURKEY"
# rejected under Rule B #15 while its "NATIONAL MENU - CHICKEN", "- FISH",
# "- CHEESE", "- BEANS" and "- TOMATO" were pursued as hardware, all under the
# same NAICS on the same day.
_FOOD_PRODUCT_WORDS = (
    # catalogue and category words
    r"menus?", r"subsistence", r"provisions", r"foodstuffs?", r"food items?",
    r"groceries", r"grocery", r"rations?", r"meals?",
    # protein
    r"meats?", r"poultry", r"chicken", r"turkey", r"beef", r"pork", r"ham",
    r"fish", r"seafood", r"shrimp", r"tuna", r"salmon", r"eggs?",
    # dairy
    r"milk", r"dairy", r"cheese", r"yogh?urt", r"butter",
    # produce and staples
    r"produce", r"fruits?", r"vegetables?", r"tomato(?:es)?", r"potato(?:es)?",
    r"beans?", r"rice", r"pasta", r"bread", r"cereals?", r"flour",
    # prepared and drink
    r"snacks?", r"beverages?", r"juice", r"coffee", r"tea",
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
    naics_full = _naics_full(naics_code)

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
        if _service_signal_present(hay, naics_full):
            return "SERVICE"
        return "HARDWARE"

    # Known service NAICS ranges → SERVICE
    if prefix is not None and (
        prefix in (115,) or 481 <= prefix <= 928
    ):
        return "SERVICE"

    # No / unknown NAICS — decide by title content.
    if _service_signal_present(hay, naics_full):
        return "SERVICE"
    if _has(hay, *_HARDWARE_TITLE_SIGNALS):
        return "HARDWARE"
    # Ambiguous with no NAICS and no signals — treat as service (routes to the
    # unlisted-service REJECT rather than auto-pursuing as hardware).
    return "SERVICE"


def _service_signal_present(hay: str, naics_full: str = "") -> bool:
    """
    True if the text carries a decisive SERVICE signal that should override a
    manufacturing-NAICS hardware default: a Rule C equipment+action match, a
    general maintenance/repair/overhaul/inspection verb, or vessel/drydock work.
    """
    if _has(hay, *_SERVICE_VERBS):
        return True
    if _has(hay, "drydock", "dry dock", "dockside", "ssra", "vessel"):
        return True
    if _check_rule_c(hay, naics_full) is not None:
        return True
    return False


# ===========================================================================
# Public API
# ===========================================================================

# ===========================================================================
# STEP 4a — Hard Reject Gate (spec B4)
# ===========================================================================
# NAICS ranges only. No keywords, no bid content, nothing product-specific: the
# gate has to give the same answer for a bid written next year about a product
# nobody has heard of yet, and a keyword cannot promise that.
#
# The 238 split is the part to be careful with. 236xxx and 237xxx reject on
# their three-character sector, but 238xxx does NOT — 238210 (cable), 238220
# (HVAC), 238290 (industrial hardware) and 238390 (other) are Rule C
# installation trades and must pass the gate. Only the six-character codes
# listed below are rejected, which is why six characters are checked first.

REJECT_3CHAR: dict[str, str] = {
    "236": "Rule B #5 — Construction & Demolition Services",
    "237": "Rule B #5 — Construction & Demolition Services",
    "511": "Rule B #3 — Management Software / Publishing",
    "513": "Rule B #3 — Management Software / Publishing",
    "611": "Rule B #9 — Training Services",
    "112": "Out-of-scope — Animal Production",
    "114": "Out-of-scope — Fishing and Hunting",
}

REJECT_6CHAR: dict[str, str] = {
    "238320": "Rule B #5 — Painting & Wall Covering",
    "238330": "Rule B #5 — Flooring Contractors",
    "238140": "Rule B #5 — Masonry Contractors",
    "238170": "Rule B #5 — Siding Contractors",
    "238910": "Rule B #5 — Site Preparation",
    "238310": "Rule B #5 — Drywall & Insulation",
    "238370": "Rule B #5 — Plumbing & Building Construction",
    "561720": "Rule B #10 — Custodial Services",
    "561730": "Out-of-scope — Landscaping Services",
    "561790": "Out-of-scope — Other Building Services",
    "541380": "Rule B #1 — Testing Laboratory Services",
    "541511": "Out-of-scope — IT Custom Programming Services",
    "541513": "Out-of-scope — Computer Facilities Management",
    "541519": "Out-of-scope — Other Computer Services",
    "115310": "Out-of-scope — Forestry Support Activities",
    "562991": "Out-of-scope — Septic Tank / Waste Services",
}


def _naics_digits(naics_code: str) -> str:
    """The code as bare digits — "541519 — Other Services" -> "541519"."""
    return re.sub(r"\D", "", str(naics_code or ""))


def _check_hard_reject_gate(naics: str) -> str | None:
    """The rejection reason for this NAICS, or None if it passes the gate.

    Six characters before three, because 238 is split: the six-character table
    rejects painting, flooring, masonry and the rest, while 238210/238220/
    238290/238390 fall through to the scorer as the Rule C installation trades
    they are. Matching the three-character prefix first would reject every one
    of them, including the HVAC bids the spec's own test case protects.
    """
    digits = _naics_digits(naics)
    if not digits:
        return None
    reason = REJECT_6CHAR.get(digits[:6])
    if reason:
        return reason
    return REJECT_3CHAR.get(digits[:3])


# ===========================================================================
# STEP 4b — Structural scoring (spec B5)
# ===========================================================================
# Four dimensions, all structural. The constraint the spec opens with is the
# whole design: "would this rule still be correct if the specific product
# changed but the NAICS and structure stayed the same?" Every signal below is a
# NAICS band, a FAR-standard phrase, a title token (P/N, NSN, QTY) or a verb
# stem — never a product, a brand, or a domain vocabulary.

#: Dimension 1 — the more specific band wins. 3346x sits inside 311–339, so
#: checking the long prefixes first is what makes "hardware-adjacent, verify"
#: mean anything: read the other way round it would score 1.00 like any other
#: manufacturer.
_NAICS_BANDS: tuple[tuple[tuple[str, ...], float], ...] = (
    (("2381", "2382", "2383", "2386"), 0.85),   # Rule C installation trades
    (("3346", "5616", "8113"), 0.65),           # hardware-adjacent — verify
)

# Prefixed `_SCORE_` deliberately. `_SERVICE_VERBS` is already a module-level
# *tuple* further up (line ~348), unpacked with `*` by the Rule B and Rule C
# checks — defining a regex under the same name here silently rebound it and
# broke every one of those calls, which is precisely the "no existing decision
# changes" the spec's acceptance criterion 3 forbids. These three belong to the
# scorer and say so.
_SCORE_PRODUCT_VERBS = re.compile(r"(purchas|supply|procur|acquir|furnish|deliver)", re.I)
_SCORE_SERVICE_VERBS = re.compile(r"(maintain|repair|overhaul|inspect|calibrat|clean|survey)", re.I)
_SCORE_INSTALL_VERBS = re.compile(r"(install|replac|upgrade)", re.I)

_PN_NSN_RE = re.compile(r"\bP/?N\b|\bNSN\b|\bNIIN\b", re.I)
_QTY_RE = re.compile(r"\bQTY\b|\bquantity\b", re.I)

#: How short a description has to be before it counts as absent. A bid with no
#: description is a bid with no structure to read, and dimensions 2 and 4 both
#: penalise it rather than scoring it neutral.
_BLANK_DESCRIPTION_CHARS = 30


def _description_opening(full_text: str, chars: int = 300) -> str:
    """The first `chars` of the description section, never attachment text.

    Deliberately a copy of `ollama_bridge.get_description_opening` rather than an
    import of it: this module is pure — logging and re — and importing the bridge
    would pull `requests` into an engine that has to stay callable with no
    network stack present.
    """
    marker = "=== Description ==="
    text = full_text or ""
    start = text.find(marker)
    if start == -1:
        raw = text[:chars]
    else:
        content = text.find("\n", start) + 1
        nxt = text.find("===", content)
        raw = text[content:nxt] if nxt != -1 else text[content:]
    return raw[:chars].replace("\n", " ").strip()


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _score_naics_alignment(naics: str) -> float:
    """Dimension 1 (40%) — which sector the buyer filed this under."""
    digits = _naics_digits(naics)
    if not digits:
        return 0.25
    for prefixes, score in _NAICS_BANDS:
        if digits.startswith(prefixes):
            return score
    if _is_manufacturing(_naics_prefix(digits)):
        return 1.00
    return 0.25


def _score_procurement_structure(title: str, description: str) -> float:
    """Dimension 2 (35%) — FAR-standard phrasing and title tokens.

    These are phrases the FAR itself makes a contracting officer write.
    "Commercial products" and "commercial services" are the two halves of FAR
    12's own distinction, and a statement of work is what a services buy carries
    instead of a part number — so each says a great deal about the shape of the
    requirement without naming anything being bought.
    """
    desc = (description or "").lower()
    title_l = (title or "").lower()
    score = 0.0
    if "commercial products" in desc:
        score += 0.50
    if _PN_NSN_RE.search(title or ""):
        score += 0.30
    if _QTY_RE.search(title or ""):
        score += 0.20
    if "brand name" in title_l:
        score += 0.25
    if "commercial services" in desc:
        score -= 0.30
    if "statement of work" in desc or "pws" in desc:
        score -= 0.40
    if len(desc.strip()) < _BLANK_DESCRIPTION_CHARS:
        score -= 0.50
    return _clamp(score)


def _score_primary_verb(title: str) -> float:
    """Dimension 3 (15%) — what the title says is being done.

    Verb stems, not product words: "repair" reads the same whether the thing
    repaired is a pump or a radar, which is exactly the property the spec's
    design constraint asks for.
    """
    hay = title or ""
    product = bool(_SCORE_PRODUCT_VERBS.search(hay))
    service = bool(_SCORE_SERVICE_VERBS.search(hay))
    install = bool(_SCORE_INSTALL_VERBS.search(hay))
    if product and service:
        return 0.35
    if product:
        return 1.00
    if service:
        return 0.00
    if install:
        return 0.75
    return 0.50


def _score_scope_clarity(description: str) -> float:
    """Dimension 4 (10%) — is the requirement stated precisely enough to quote?"""
    desc = (description or "").lower()
    score = 0.60
    if any(word in desc for word in ("technical spec", "drawing", "specification")):
        score += 0.30
    if len(desc.strip()) < _BLANK_DESCRIPTION_CHARS:
        score -= 0.40
    return _clamp(score)


#: The bands the total is read against (spec B5). A bid between them is the only
#: thing the model is ever asked about.
PURSUE_THRESHOLD = 0.80
REJECT_THRESHOLD = 0.40


def _compute_structural_score(
    title: str, description: str, naics: str, due_date: str | None = None
) -> dict:
    """The four dimensions and their weighted total.

    `due_date` is accepted because the spec's Step 4b call passes it and B6
    anticipates new dimensions; nothing reads it today, and a dimension that did
    would have to be added to B5 first.
    """
    d1 = _score_naics_alignment(naics)
    d2 = _score_procurement_structure(title, description)
    d3 = _score_primary_verb(title)
    d4 = _score_scope_clarity(description)
    total = 0.40 * d1 + 0.35 * d2 + 0.15 * d3 + 0.10 * d4
    return {
        "naics_alignment": round(d1, 4),
        "procurement_structure": round(d2, 4),
        "primary_verb": round(d3, 4),
        "scope_clarity": round(d4, 4),
        "total": round(total, 4),
    }


def _build_reason(decision: str, scores: dict, naics: str) -> str:
    """Why the scorer landed where it did, in one readable line.

    Names the dimensions rather than a rule number, because at this step there
    is no rule — reaching it is precisely what "no rule matched" means.
    """
    shape = (
        f"NAICS {scores['naics_alignment']:.2f} / structure "
        f"{scores['procurement_structure']:.2f} / verb {scores['primary_verb']:.2f} / "
        f"scope {scores['scope_clarity']:.2f}"
    )
    verdict = "product-shaped bid" if decision == "PURSUE" else "no product signal"
    return f"Structural score {scores['total']:.2f} — {verdict} ({shape})"


def _decide(
    bid_id: str,
    full_text: str,
    config: dict,
    naics_code: str = "",
    title: str = "",
    requirement_hint: str | None = None,
    naics_title: str = "",
    resolver=None,
    binary: bool = False,
) -> dict:
    """
    Evaluate a bid per Company_Bid_Selection_Criteria.docx (company decision guide).

    Parameters
    ----------
    bid_id     : Unique bid identifier (passed through to the result).
    full_text  : Combined description + document text.
    config     : The ``sam`` section of config.yml (must contain ``evaluation``).
    naics_code : NAICS code string (primary hardware/service signal).
    title      : Notice title (most reliable categorisation signal).
    requirement_hint :
        Optional "HARDWARE" from a portal that has *structural* evidence of the
        requirement type — evidence stronger than anything inferrable from a
        title and a NAICS code. Unison passes it when a buy carries a Line
        Item(s) table of physical products with quantities and units; SAM.gov
        has no equivalent and never passes it, so omitting it leaves every
        SAM decision exactly as it was.

        It can only PROMOTE a bid to HARDWARE, never demote one to SERVICE, and
        it is applied at STEP 1 only — after the kill-word, R&D, marine-vessel
        and rental sieves, and before the food sub-check. A hinted bid is
        therefore still rejected by every Rule B override that precedes or
        follows classification; the hint decides *what* is being procured, it
        does not decide the bid.

        This exists because NAICS alone misreads reseller/distributor codes: a
        buy for laptops and cables under 541519 ("IT Value Added Resellers")
        classifies as SERVICE on its NAICS band, then rejects on location —
        precisely the location-first outcome §1 of the guide forbids.

    Returns
    -------
    dict with keys: bid_id, decision, reason, requirement_type, rule,
    location, stopped_at_step.

    decision ∈ {PURSUE, REJECT, MANUAL_REVIEW}. MANUAL_REVIEW is reserved for an
    unlisted service (neither Rule B nor Rule C) performed in the US Mainland;
    every other path resolves to PURSUE or REJECT.
    The ``reason`` field always uses one of the standard phrases.
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

    # ── STEP 0b: Rental override (Rule B #6) — beats hardware AND Rule C ──────
    # Fix 2: rented equipment is out of scope no matter what is rented or where
    # it is performed, so a "rental" primary scope rejects immediately, ahead of
    # the hardware gate and any Rule C matching.
    if _is_rental_primary(hay, full_text):
        result.update(
            decision="REJECT", stopped_at_step=3, rule="B6",
            requirement_type="SERVICE", reason=reason_rule_b(6, RULE_B[6]),
        )
        logger.info(f"[EVAL] {bid_id} -> REJECT @ Rule B #6 (rental override)")
        return result

    # ── STEP 1: Hardware vs Service ──────────────────────────────────────────
    req_type = _classify_requirement(hay, naics_code, full_text)
    # A caller with structural evidence of a supply (see `requirement_hint`) may
    # promote SERVICE to HARDWARE here, and only here: every Rule B sieve that
    # rejects outright has already run, and the food sub-check below still does.
    # The hint is deliberately one-way — nothing may demote HARDWARE to SERVICE,
    # so it can never turn a Rule A pursue into a location-gated reject.
    if requirement_hint == "HARDWARE" and req_type != "HARDWARE":
        logger.info(f"[EVAL] {bid_id} -> requirement hint promoted SERVICE to HARDWARE")
        req_type = "HARDWARE"
        result["hinted"] = True
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
    rule_c = _check_rule_c(hay, naics_full)
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

    # ── Service on neither list ─────────────────────────────────────────────
    # Reaching here means the bid matched no rule at all: not a kill-word, not
    # hardware, not Rule B, not Rule C.
    #
    # OUTSIDE US Mainland this is still an outright REJECT (spec B7: "Unlisted
    # service → REJECT"). The location alone disqualifies it, so it never
    # reaches the gate or the scorer — and keeping this branch is also what
    # holds acceptance criterion 3, since sending these through the scorer could
    # promote an existing REJECT to PURSUE.
    if location != "US_MAINLAND":
        result.update(
            decision="REJECT", stopped_at_step=4, rule="none",
            reason=reason_not_listed_outside(),
        )
        logger.info(f"[EVAL] {bid_id} -> REJECT (unlisted service, outside US Mainland)")
        return result

    # INSIDE US Mainland this is MANUAL_REVIEW — a third decision state that
    # puts the bid back in front of a person — unless the caller asked for a
    # binary answer.
    #
    # `binary` is opt-in rather than the default because this engine is shared.
    # SAM_Binary_Engine_Prompt_and_Criteria.pdf is a SAM document, but
    # `evaluate_bid` also decides every Philadelphia and Unison bid, and both of
    # those portals have their own criteria, their own MANUAL_REVIEW rows and
    # their own amber styling for them. Removing the state for everyone would
    # silently rewrite two products nobody asked about.
    if not binary:
        result.update(
            decision="MANUAL_REVIEW", stopped_at_step=4, rule="none",
            reason=reason_manual_review(),
        )
        logger.info(f"[EVAL] {bid_id} -> MANUAL_REVIEW (unlisted service, US Mainland)")
        return result

    # From here down is the binary engine (spec Part A), in two steps.
    #
    # ── STEP 4a: hard reject gate ───────────────────────────────────────────
    hard_reject = _check_hard_reject_gate(naics_code)
    if hard_reject:
        result.update(
            decision="REJECT", stopped_at_step="4a", rule="naics_gate",
            reason=hard_reject, score=0.05,
        )
        logger.info(f"[EVAL] {bid_id} -> REJECT @ step 4a ({hard_reject})")
        return result

    # ── STEP 4b: structural scoring ─────────────────────────────────────────
    scores = _compute_structural_score(
        title=classify_text,
        description=_description_opening(full_text),
        naics=naics_code,
    )
    total = scores["total"]

    if total >= PURSUE_THRESHOLD:
        decision = "PURSUE"
    elif total <= REJECT_THRESHOLD:
        decision = "REJECT"
    else:
        # The uncertain band, and the only place the model is consulted. The
        # resolver is injected rather than imported: this module is pure, and an
        # engine that reaches for the network on its own cannot be evaluated in
        # a test or on a machine with no Ollama.
        #
        # Falling back to REJECT when nothing resolves is the spec's own
        # instruction — `(ollama or {}).get('decision', 'REJECT')`. It is the
        # cost of a binary contract: with no model reachable, an ambiguous bid
        # is dropped rather than shown to anyone.
        resolved = None
        if resolver is not None:
            try:
                resolved = resolver(
                    title=title, naics_code=naics_code, naics_title=naics_title,
                    full_text=full_text, result=result, scores=scores,
                )
            except Exception as exc:  # noqa: BLE001 — a resolver is never worth a failed bid
                logger.warning(f"[EVAL] {bid_id} resolver failed: {exc}")
        decision = (resolved or {}).get("decision") or "REJECT"
        if decision == "MANUAL_REVIEW":
            # The engine is binary. A resolver that will not commit is a
            # resolver that did not answer.
            decision = "REJECT"

    result.update(
        decision=decision, stopped_at_step="4b", rule="structural_score",
        reason=_build_reason(decision, scores, naics_code), score=total,
        score_breakdown=scores,
    )
    logger.info(f"[EVAL] {bid_id} -> {decision} @ step 4b (score {total:.2f})")
    return result


# ===========================================================================
# STEP 5 — the JSON output schema (spec Part A step 5)
# ===========================================================================

#: `stopped_at_step` -> the spec's `decision_path` name.
#:
#: One value is not in the spec's enum: `step4_location_gate`. The spec's Part A
#: pseudocode drops the location split when it replaces the MANUAL_REVIEW block,
#: but Part B7 keeps it — "Outside US Mainland … Unlisted service → REJECT" — and
#: so does this engine, because routing those bids through the scorer could
#: promote an existing REJECT to PURSUE and break acceptance criterion 3. The
#: branch is real, so it is named rather than folded into a path it is not.
_DECISION_PATHS: dict[object, str] = {
    0: "step0_killword",
    2: "step1_hardware",
    3: "step2_rule_b",
    4: "step4_location_gate",
    5: "step3_rule_c",
    "4a": "step4a_naics_gate",
    "4b": "step4b_structural_score",
}

#: The confidence a deterministic rule carries. A rule that matched is not a
#: guess, so the only paths with a real number below 1.0 are the gate (a flat
#: 0.05, per the spec) and the scorer (its own total).
_RULE_CONFIDENCE = 1.0

BINARY_DECISIONS = ("PURSUE", "REJECT")


def _apply_schema(result: dict) -> dict:
    """Stamp the spec's output schema onto a ladder result, in place.

    Additive on purpose. `decision`, `reason`, `rule`, `location` and
    `stopped_at_step` all stay exactly as they were, because `runner.py`,
    `export.py`, `models.py` and the console read them — the spec's "one
    targeted change" is about not rewriting those consumers, not about
    withholding the new fields from them.
    """
    decision = result.get("decision")
    reason = result.get("reason") or ""
    score = result.get("score")
    if score is None:
        score = _RULE_CONFIDENCE

    result["solicitation_id"] = result.get("bid_id")
    result["final_decision"] = decision
    result["confidence_score"] = round(float(score), 4)
    result["decision_path"] = _DECISION_PATHS.get(
        result.get("stopped_at_step"), "step4b_structural_score"
    )
    result["match_reasons"] = [reason] if decision == "PURSUE" and reason else []
    result["rejection_reasons"] = [reason] if decision == "REJECT" and reason else []
    result.setdefault("score_breakdown", {})
    return result


def evaluate_bid(*args, **kwargs) -> dict:
    """Evaluate one bid through the decision ladder, then stamp the output schema.

    With `binary=True` the decision is always PURSUE or REJECT — never anything
    else. That is what SAM asks for, and this wrapper guarantees two things:

    **MANUAL_REVIEW is unreachable.** The ladder does not produce it for a binary
    caller, and this is the belt to that brace: anything that is not one of the
    two binary values is coerced to REJECT and logged loudly. A third state
    leaking downstream is the failure the change exists to prevent, and it
    should be impossible rather than merely unlikely.

    Without `binary` the ladder is exactly what it was — three decision states,
    MANUAL_REVIEW included — because Philadelphia and Unison share this engine
    and neither asked for a binary answer.

    **Every bid carries the full schema.** `solicitation_id`, `final_decision`,
    `confidence_score`, `decision_path`, `match_reasons`, `rejection_reasons`
    and `score_breakdown` are present on every result, whichever step decided
    it — added alongside the existing keys, never in place of them.
    """
    result = _decide(*args, **kwargs)

    if kwargs.get("binary") and result.get("decision") not in BINARY_DECISIONS:
        logger.error(
            "[EVAL] %s produced a non-binary decision %r at step %r — coercing to "
            "REJECT; the ladder has a path that does not resolve",
            result.get("bid_id"), result.get("decision"), result.get("stopped_at_step"),
        )
        result["decision"] = "REJECT"
        result["reason"] = result.get("reason") or "Unresolved by the decision ladder"

    return _apply_schema(result)
