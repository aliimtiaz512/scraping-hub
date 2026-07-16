"""Curated MFMP search niches: commodity codes plus screening keywords.

Each niche carries both search paths the portal supports, and a run uses exactly
one of them (see SearchMode in the router):

  codes    — the MFMP commodity codes to select in Advanced Search.
  keywords — free-text terms, each searched separately in its own run pass.

Source: the recommended codes/keywords brief. Codes are the class-level and
specific commodity codes that have appeared in MFMP opportunities or align
closely with the requested service areas; keywords are that niche's
"search and screen for" terms.
"""

CATEGORIES = {
    "graphic_design_creative": {
        "label": "Graphic Design & Creative Services",
        "codes": [
            {"code": "82141501", "title": "Layout or graphics editing services"},
            {"code": "82141502", "title": "Art design or graphics"},
            {"code": "82141600", "title": "Graphic display services"},
            {"code": "82121501", "title": "Planning or layout of graphic production"},
        ],
        "keywords": [
            "graphic design",
            "creative services",
            "branding",
            "visual communications",
            "publication design",
            "layout",
            "annual report",
            "signage",
            "exhibit design",
            "presentation design",
            "artwork",
        ],
    },
    "digital_marketing_outreach": {
        "label": "Digital Marketing, Advertising & Outreach",
        "codes": [
            {"code": "80171600", "title": "Publicity and marketing support services"},
            {"code": "80171602", "title": "Online and social media publicity service"},
            {"code": "80171603", "title": "Publicity and marketing advisory service"},
            {"code": "80171604", "title": "Public information campaign service"},
            {"code": "82101603", "title": "Internet advertising"},
            {"code": "82101800", "title": "Advertising agency services"},
            {"code": "82101801", "title": "Advertising campaign services"},
            {"code": "82101900", "title": "Media placement and fulfillment"},
        ],
        "keywords": [
            "digital marketing",
            "advertising",
            "social media",
            "public relations",
            "strategic communications",
            "community outreach",
            "media buying",
            "media planning",
            "SEO",
            "paid media",
            "content marketing",
            "email marketing",
            "campaign management",
        ],
    },
    "printing_production": {
        "label": "Printing & Print Production",
        "codes": [
            {"code": "82121500", "title": "Printing"},
            {"code": "82121503", "title": "Digital printing"},
            {"code": "82121505", "title": "Promotional or advertising printing"},
            {"code": "82121506", "title": "Publication printing"},
            {"code": "82121507", "title": "Stationery or business form printing"},
            {"code": "82121700", "title": "Photocopying"},
        ],
        "keywords": [
            "printing",
            "commercial printing",
            "digital printing",
            "publication printing",
            "brochures",
            "booklets",
            "annual reports",
            "postcards",
            "direct mail",
            "large-format printing",
            "banners",
            "signage",
            "fulfillment",
        ],
    },
    "software_web_development": {
        "label": "Software, Web & Application Development",
        "codes": [
            {"code": "81111500", "title": "Software or hardware engineering"},
            {"code": "81111503", "title": "Systems integration design"},
            {"code": "81111504", "title": "Application programming services"},
            {"code": "81111508", "title": "Application implementation services"},
            {"code": "81111509", "title": "Internet or intranet client application development services"},
            {"code": "81111510", "title": "Internet or intranet server application development services"},
            {"code": "81112103", "title": "World Wide Web site design services"},
        ],
        "keywords": [
            "software development",
            "application development",
            "web development",
            "website redesign",
            "system modernization",
            "systems integration",
            "portal development",
            "mobile application",
            "SaaS",
            "cloud platform",
            "database development",
            "API integration",
            "content management system",
        ],
    },
    "ai_data_automation": {
        "label": "AI, Data & Automation",
        "codes": [
            {"code": "43232314", "title": "Business intelligence and data analysis software"},
            {"code": "43232400", "title": "Development software"},
            {"code": "43232403", "title": "Enterprise application integration software"},
            {"code": "43232701", "title": "Application server software"},
            {"code": "81111504", "title": "Application programming services"},
            {"code": "81111705", "title": "Systems architecture"},
        ],
        "keywords": [
            "artificial intelligence",
            "generative AI",
            "machine learning",
            "predictive analytics",
            "natural language processing",
            "NLP",
            "chatbot",
            "virtual assistant",
            "computer vision",
            "intelligent automation",
            "document processing",
            "business intelligence",
            "large language model",
        ],
    },
}


def get_codes(category_key: str) -> list[str]:
    """Every commodity code in a niche."""
    return [entry["code"] for entry in CATEGORIES[category_key]["codes"]]


def get_keywords(category_key: str) -> list[str]:
    """Every screening keyword in a niche."""
    return list(CATEGORIES[category_key]["keywords"])
