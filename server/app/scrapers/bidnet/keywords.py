"""Curated BidNet Direct keyword catalog, organized by niche and tier.

Source: BidNet_Direct_Keyword_Reference.docx (Rizviz International Impex bid
sourcing pipeline). Each niche (AI/ML, Web Scraping, UI/UX) has a **core** tier
(high-yield, run first) and an **extended** tier (specialized). The scraper
searches each selected keyword *separately* (never concatenated) — per the
reference, one keyword per query gives the best results — and folders the
results per niche+tier (see `group_keywords`).
"""

# niche key -> {label, slug (used in folder names), core[], extended[]}
# Each keyword entry is {term, notes}. Tier is implied by the list it lives in.
NICHES: dict[str, dict] = {
    "ai_ml": {
        "label": "AI / ML",
        "slug": "AI-ML",
        "core": [
            {"term": "Artificial Intelligence", "notes": "Highest yield, low noise. Run before the bare acronym 'AI'."},
            {"term": "Machine Learning", "notes": "High yield; prefer full phrase over 'ML' to avoid acronym collisions."},
            {"term": "Deep Learning", "notes": "Moderate yield; frequently paired with 'neural network' in RFP text."},
            {"term": "Natural Language Processing", "notes": "Prefer full phrase; also try 'NLP' as a secondary/manual-review term."},
            {"term": "Generative AI", "notes": "Fast-growing category; run alongside 'Artificial Intelligence'."},
            {"term": "Large Language Model", "notes": "Also try 'LLM'; increasing frequency in RFPs since 2024."},
            {"term": "Chatbot", "notes": "High yield; also try 'virtual assistant' and 'conversational AI'."},
            {"term": "Predictive Analytics", "notes": "Common in public-sector data/analytics RFPs."},
            {"term": "Data Analytics", "notes": "Broad; expect to manually filter results for AI/ML relevance."},
            {"term": "Data Science", "notes": "Broad; often appears in staffing/consulting RFPs."},
            {"term": "Computer Vision", "notes": "Moderate yield; pair with 'image recognition' for coverage."},
            {"term": "AI Agent", "notes": "Growing category; also try 'AI agents' plural and 'agentic AI'."},
        ],
        "extended": [
            {"term": "Robotic Process Automation", "notes": "Also search 'RPA' as a secondary term."},
            {"term": "Intelligent Automation", "notes": "Adjacent to RPA; catches automation-focused RFPs."},
            {"term": "Cognitive Computing", "notes": "Lower yield but appears in older/legacy postings."},
            {"term": "Speech Recognition", "notes": "Pair with 'voice recognition' and 'transcription'."},
            {"term": "Text Mining", "notes": "Pair with 'text analytics' and 'sentiment analysis'."},
            {"term": "Sentiment Analysis", "notes": "Niche but precise; low false-positive rate."},
            {"term": "Facial Recognition", "notes": "Common in public-safety/security RFPs."},
            {"term": "Anomaly Detection", "notes": "Appears in fraud-detection and cybersecurity RFPs."},
            {"term": "Fraud Detection", "notes": "Pair with 'AI' or 'machine learning' to keep it in scope."},
            {"term": "Recommendation Engine", "notes": "Low volume; mostly retail/citizen-portal RFPs."},
            {"term": "AI Platform", "notes": "Catches procurement of AI tooling/licenses rather than services."},
            {"term": "AI-Powered", "notes": "Useful as a modifier phrase inside longer RFP titles."},
            {"term": "Optical Character Recognition", "notes": "Also search 'OCR'; common in document-digitization RFPs."},
        ],
    },
    "web_scraping": {
        "label": "Web Scraping",
        "slug": "Web-Scraping",
        "core": [
            {"term": "Web Scraping", "notes": "Direct, high-precision term."},
            {"term": "Data Scraping", "notes": "Synonym; occasionally used interchangeably with web scraping."},
            {"term": "Data Extraction", "notes": "Broader; catches ETL and document-extraction RFPs too."},
        ],
        "extended": [
            {"term": "Data Harvesting", "notes": "Lower volume; some public-sector RFPs use this phrasing."},
            {"term": "Automated Data Collection", "notes": "Good full-phrase alternative when 'scraping' isn't used."},
            {"term": "Web Crawling", "notes": "Occasionally used instead of 'scraping' in technical RFPs."},
        ],
    },
    "uiux": {
        "label": "UI / UX",
        "slug": "UI-UX",
        "core": [
            {"term": "UI/UX Design", "notes": "Best starting phrase — precise and high yield."},
            {"term": "User Interface Design", "notes": "Full phrase; safer than bare 'UI' (collides with Unemployment Insurance)."},
            {"term": "User Experience Design", "notes": "Full phrase; safer than bare 'UX'."},
            {"term": "Website Design", "notes": "High yield; expect general web-dev noise to filter manually."},
            {"term": "Website Redesign", "notes": "Very common RFP title phrasing for modernization projects."},
            {"term": "Web Application Design", "notes": "Narrower than 'website design'; catches portal/app work."},
            {"term": "Mobile App Design", "notes": "Use if mobile deliverables are in scope."},
            {"term": "Responsive Design", "notes": "Common qualifier phrase inside broader web RFPs."},
            {"term": "Human-Centered Design", "notes": "Increasingly used in government digital-services RFPs."},
            {"term": "Digital Experience", "notes": "Broad modern phrase; often paired with 'platform' or 'strategy'."},
        ],
        "extended": [
            {"term": "Wireframing", "notes": "Niche but precise; low false-positive rate."},
            {"term": "Prototyping", "notes": "Pair with 'UI' or 'design' to keep it web/app-relevant."},
            {"term": "Usability Testing", "notes": "Appears in accessibility and CX-focused RFPs."},
            {"term": "Interaction Design", "notes": "Lower volume, precise term."},
            {"term": "Visual Design", "notes": "Broad; often paired with branding RFPs."},
            {"term": "Design System", "notes": "Growing category in agency/enterprise digital-services RFPs."},
            {"term": "Information Architecture", "notes": "Common in large portal/website overhaul RFPs."},
            {"term": "Portal Design", "notes": "Directly relevant to citizen/employee portal projects."},
            {"term": "Dashboard Design", "notes": "Relevant to data-visualization and reporting-tool RFPs."},
            {"term": "Website Modernization", "notes": "Common government phrasing; often bundled with UI/UX scope."},
            {"term": "Section 508 Compliance", "notes": "Accessibility requirement frequently bundled with UI/UX RFPs."},
            {"term": "ADA Compliance", "notes": "Accessibility qualifier; useful to confirm relevance."},
        ],
    },
}

# Folder name for keywords typed by hand (no niche/tier).
CUSTOM_FOLDER = "Bidnetdirect_Custom"


def get_niche_catalog() -> list[dict]:
    """Return the catalog as an ordered list of niches for the API/frontend.

    Each niche carries its `core` and `extended` keyword lists; the frontend
    renders them as two sections and derives the Tier-1 (core) badge from which
    list a term is in.
    """
    return [
        {
            "key": key,
            "label": niche["label"],
            "slug": niche["slug"],
            "core": niche["core"],
            "extended": niche["extended"],
        }
        for key, niche in NICHES.items()
    ]


# term -> {niche_key, label, slug, tier}. Built once; terms are unique across the
# whole catalog, so a term maps to exactly one niche+tier.
_TERM_INDEX: dict[str, dict] = {}
for _key, _niche in NICHES.items():
    for _tier in ("core", "extended"):
        for _kw in _niche[_tier]:
            _TERM_INDEX[_kw["term"]] = {
                "niche_key": _key,
                "label": _niche["label"],
                "slug": _niche["slug"],
                "tier": _tier,
            }

# Flat set of every catalog term (the scrape endpoint still accepts arbitrary
# custom terms not in this set).
VALID_TERMS: set[str] = set(_TERM_INDEX)


def _folder_name(slug: str, tier: str) -> str:
    return f"Bidnetdirect_{slug}_{tier}"


def group_keywords(keywords: list[str]) -> list[dict]:
    """Split a flat selected-keyword list into ordered niche+tier groups.

    Each returned group is a self-contained scrape+storage unit:
        {niche_key, label, slug, tier, folder_name, keywords: [...]}
    Catalog terms map to their niche+tier; anything not in the catalog is
    collected into a single 'custom' group (Bidnetdirect_Custom). Groups follow
    catalog order (niche, then core before extended), custom last; keyword order
    within a group follows the catalog (custom follows input order). Blank and
    duplicate terms are dropped.
    """
    cleaned = list(dict.fromkeys(kw.strip() for kw in keywords if kw.strip()))
    chosen = set(cleaned)

    groups: list[dict] = []
    # Catalog groups in a stable order.
    for niche_key, niche in NICHES.items():
        for tier in ("core", "extended"):
            terms = [kw["term"] for kw in niche[tier] if kw["term"] in chosen]
            if terms:
                groups.append({
                    "niche_key": niche_key,
                    "label": f"{niche['label']} · {tier.capitalize()}",
                    "slug": niche["slug"],
                    "tier": tier,
                    "folder_name": _folder_name(niche["slug"], tier),
                    "keywords": terms,
                })

    # Custom terms (not in the catalog), preserving input order.
    custom = [term for term in cleaned if term not in VALID_TERMS]
    if custom:
        groups.append({
            "niche_key": "custom",
            "label": "Custom",
            "slug": "Custom",
            "tier": "custom",
            "folder_name": CUSTOM_FOLDER,
            "keywords": custom,
        })

    return groups
