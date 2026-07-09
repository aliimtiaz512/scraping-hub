"""Curated BidNet Direct keyword catalog, grouped by sourcing track.

Source: BidNet_Direct_Keyword_Reference.docx (Rizviz International Impex bid
sourcing pipeline). Terms are grouped and tiered exactly as in that reference:
tier1 = core / high-yield (run first), tier2 = extended / specialized.

The scraper searches each selected keyword *separately* (never concatenated) —
per the reference, one keyword per query gives the best results.
"""

KEYWORD_GROUPS: dict[str, dict] = {
    "ai_core": {
        "label": "AI / ML — Core",
        "keywords": [
            {"term": "Artificial Intelligence", "tier": "tier1", "notes": "Highest yield, low noise. Run before the bare acronym 'AI'."},
            {"term": "Machine Learning", "tier": "tier1", "notes": "High yield; prefer full phrase over 'ML' to avoid acronym collisions."},
            {"term": "Deep Learning", "tier": "tier1", "notes": "Moderate yield; frequently paired with 'neural network' in RFP text."},
            {"term": "Natural Language Processing", "tier": "tier1", "notes": "Prefer full phrase; also try 'NLP' as a secondary/manual-review term."},
            {"term": "Generative AI", "tier": "tier1", "notes": "Fast-growing category; run alongside 'Artificial Intelligence'."},
            {"term": "Large Language Model", "tier": "tier1", "notes": "Also try 'LLM'; increasing frequency in RFPs since 2024."},
            {"term": "Chatbot", "tier": "tier1", "notes": "High yield; also try 'virtual assistant' and 'conversational AI'."},
            {"term": "Predictive Analytics", "tier": "tier1", "notes": "Common in public-sector data/analytics RFPs."},
            {"term": "Data Analytics", "tier": "tier1", "notes": "Broad; expect to manually filter results for AI/ML relevance."},
            {"term": "Data Science", "tier": "tier1", "notes": "Broad; often appears in staffing/consulting RFPs."},
            {"term": "Computer Vision", "tier": "tier1", "notes": "Moderate yield; pair with 'image recognition' for coverage."},
            {"term": "AI Agent", "tier": "tier1", "notes": "Growing category; also try 'AI agents' plural and 'agentic AI'."},
        ],
    },
    "ai_extended": {
        "label": "AI / ML — Extended",
        "keywords": [
            {"term": "Robotic Process Automation", "tier": "tier2", "notes": "Also search 'RPA' as a secondary term."},
            {"term": "Intelligent Automation", "tier": "tier2", "notes": "Adjacent to RPA; catches automation-focused RFPs."},
            {"term": "Cognitive Computing", "tier": "tier2", "notes": "Lower yield but appears in older/legacy postings."},
            {"term": "Speech Recognition", "tier": "tier2", "notes": "Pair with 'voice recognition' and 'transcription'."},
            {"term": "Text Mining", "tier": "tier2", "notes": "Pair with 'text analytics' and 'sentiment analysis'."},
            {"term": "Sentiment Analysis", "tier": "tier2", "notes": "Niche but precise; low false-positive rate."},
            {"term": "Facial Recognition", "tier": "tier2", "notes": "Common in public-safety/security RFPs."},
            {"term": "Anomaly Detection", "tier": "tier2", "notes": "Appears in fraud-detection and cybersecurity RFPs."},
            {"term": "Fraud Detection", "tier": "tier2", "notes": "Pair with 'AI' or 'machine learning' to keep it in scope."},
            {"term": "Recommendation Engine", "tier": "tier2", "notes": "Low volume; mostly retail/citizen-portal RFPs."},
            {"term": "AI Platform", "tier": "tier2", "notes": "Catches procurement of AI tooling/licenses rather than services."},
            {"term": "AI-Powered", "tier": "tier2", "notes": "Useful as a modifier phrase inside longer RFP titles."},
            {"term": "Optical Character Recognition", "tier": "tier2", "notes": "Also search 'OCR'; common in document-digitization RFPs."},
        ],
    },
    "web_scraping": {
        "label": "Web Scraping / Data Extraction",
        "keywords": [
            {"term": "Web Scraping", "tier": "tier1", "notes": "Direct, high-precision term."},
            {"term": "Data Scraping", "tier": "tier1", "notes": "Synonym; occasionally used interchangeably with web scraping."},
            {"term": "Data Extraction", "tier": "tier1", "notes": "Broader; catches ETL and document-extraction RFPs too."},
            {"term": "Data Harvesting", "tier": "tier2", "notes": "Lower volume; some public-sector RFPs use this phrasing."},
            {"term": "Automated Data Collection", "tier": "tier2", "notes": "Good full-phrase alternative when 'scraping' isn't used."},
            {"term": "Web Crawling", "tier": "tier2", "notes": "Occasionally used instead of 'scraping' in technical RFPs."},
        ],
    },
    "uiux_core": {
        "label": "UI/UX — Core",
        "keywords": [
            {"term": "UI/UX Design", "tier": "tier1", "notes": "Best starting phrase — precise and high yield."},
            {"term": "User Interface Design", "tier": "tier1", "notes": "Full phrase; safer than bare 'UI' (collides with Unemployment Insurance)."},
            {"term": "User Experience Design", "tier": "tier1", "notes": "Full phrase; safer than bare 'UX'."},
            {"term": "Website Design", "tier": "tier1", "notes": "High yield; expect general web-dev noise to filter manually."},
            {"term": "Website Redesign", "tier": "tier1", "notes": "Very common RFP title phrasing for modernization projects."},
            {"term": "Web Application Design", "tier": "tier1", "notes": "Narrower than 'website design'; catches portal/app work."},
            {"term": "Mobile App Design", "tier": "tier1", "notes": "Use if mobile deliverables are in scope."},
            {"term": "Responsive Design", "tier": "tier1", "notes": "Common qualifier phrase inside broader web RFPs."},
            {"term": "Human-Centered Design", "tier": "tier1", "notes": "Increasingly used in government digital-services RFPs."},
            {"term": "Digital Experience", "tier": "tier1", "notes": "Broad modern phrase; often paired with 'platform' or 'strategy'."},
        ],
    },
    "uiux_extended": {
        "label": "UI/UX — Extended",
        "keywords": [
            {"term": "Wireframing", "tier": "tier2", "notes": "Niche but precise; low false-positive rate."},
            {"term": "Prototyping", "tier": "tier2", "notes": "Pair with 'UI' or 'design' to keep it web/app-relevant."},
            {"term": "Usability Testing", "tier": "tier2", "notes": "Appears in accessibility and CX-focused RFPs."},
            {"term": "Interaction Design", "tier": "tier2", "notes": "Lower volume, precise term."},
            {"term": "Visual Design", "tier": "tier2", "notes": "Broad; often paired with branding RFPs."},
            {"term": "Design System", "tier": "tier2", "notes": "Growing category in agency/enterprise digital-services RFPs."},
            {"term": "Information Architecture", "tier": "tier2", "notes": "Common in large portal/website overhaul RFPs."},
            {"term": "Portal Design", "tier": "tier2", "notes": "Directly relevant to citizen/employee portal projects."},
            {"term": "Dashboard Design", "tier": "tier2", "notes": "Relevant to data-visualization and reporting-tool RFPs."},
            {"term": "Website Modernization", "tier": "tier2", "notes": "Common government phrasing; often bundled with UI/UX scope."},
            {"term": "Section 508 Compliance", "tier": "tier2", "notes": "Accessibility requirement frequently bundled with UI/UX RFPs."},
            {"term": "ADA Compliance", "tier": "tier2", "notes": "Accessibility qualifier; useful to confirm relevance."},
        ],
    },
}


def get_keyword_catalog() -> list[dict]:
    """Return the catalog as an ordered list of groups for the API/frontend."""
    return [
        {"key": key, "label": group["label"], "keywords": group["keywords"]}
        for key, group in KEYWORD_GROUPS.items()
    ]


# Flat set of every catalog term (used by the frontend for grouping; the scrape
# endpoint still accepts arbitrary custom terms not in this set).
VALID_TERMS: set[str] = {
    kw["term"] for group in KEYWORD_GROUPS.values() for kw in group["keywords"]
}
