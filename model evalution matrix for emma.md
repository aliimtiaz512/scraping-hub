# EMMA Portal — Bid Selection / Evaluation Matrix

**Purpose:** Single source of truth for the PURSUE / REJECT decision logic that the EMMA bid scraping & evaluation pipeline must be configured against.

**Derived from:**
- `Company_Bid_Selection_Criteria.docx` (SAM.gov PURSUE/REJECT decision guide)
- `EMMA_Keywords.docx` (EMMA-specific rejection keyword list)

Both source documents encode **rejection logic** for out-of-scope work. This file merges them into one unified matrix for EMMA. Where the SAM.gov logic depends on data EMMA may not provide, this is flagged explicitly as an **open item** — do not assume, verify against actual EMMA data first.

---

## 1. Core Principle (inherited from SAM.gov logic)

Decisions are driven **primarily by WHAT is being procured** (requirement type), and only **secondarily by WHERE** it is performed (place of performance). This is the reverse of a location-first filter.

Two questions decide every bid, in this order:

- **Q1 — Requirement type:** Is the requirement a HARDWARE / MATERIAL supply, or a SERVICE?
- **Q2 — Scope & location:** If it is a service, is it an allowed service AND performed in the US Mainland?

> ⚠️ **Open item:** This principle assumes EMMA bids can be classified as hardware vs. service, and that a place-of-performance field exists. Both need verification (see Section 6).

---

## 2. Rule A — Hardware / Material Requirements

We pursue hardware and material-based requirements, **including those with delivery locations OUTSIDE the United States**. Place of performance does NOT disqualify a hardware bid.

**Decision: PURSUE (regardless of location).**

> ⚠️ **Assumption to verify:** It's unconfirmed whether EMMA carries a meaningful volume of hardware/material bids (as opposed to being almost entirely services). Claude Code should sample real EMMA bid data to confirm Rule A is even applicable before building classification logic for it. If EMMA is service-only, this rule can be deprioritized or stubbed out.

---

## 3. Rule B — Excluded Services (Out of Scope Everywhere)

**Merged and deduplicated** from the SAM.gov Rule B list (20 categories) and the EMMA keyword list (17 keywords). Overlapping/synonymous terms have been consolidated into a single row with source tracking so nothing is lost.

These services are out of scope **regardless of place of performance** (inside or outside the US). A bid whose primary requirement matches any row below is rejected outright.

| # | Excluded Service Category | Source |
|---|---|---|
| 1 | Maintenance, Repair and Inspection Services *(incl. "Repair and Maintenance", "On Call Services / Maintenance Services", "Inspection Services")* | SAM.gov + EMMA |
| 2 | Management Services | SAM.gov |
| 3 | Management Software | SAM.gov |
| 4 | Audit | SAM.gov + EMMA |
| 5 | Construction & Demolition Services *(incl. "Construction", "Renovation", "Improvement Project")* | SAM.gov + EMMA |
| 6 | Rental of Equipment | SAM.gov |
| 7 | Lease of Equipment | SAM.gov |
| 8 | Waste Management Services | SAM.gov |
| 9 | Promotional Services | SAM.gov |
| 10 | Training Services | SAM.gov |
| 11 | Custodial Services | SAM.gov + EMMA |
| 12 | Janitorial Services | EMMA |
| 13 | Engineering Support Services *(incl. "Consulting / Engineering Services")* | SAM.gov + EMMA |
| 14 | Hotel Room Booking and Lodging | SAM.gov |
| 15 | Yellow Ribbon | SAM.gov |
| 16 | Food Items | SAM.gov |
| 17 | Religious & Education Coordinator | SAM.gov |
| 18 | Real Estate *(incl. "Property Management")* | SAM.gov + EMMA |
| 19 | Aircraft Lavatory Services | SAM.gov |
| 20 | Marine Vessel Upgrade | SAM.gov |
| 21 | Research & Development | SAM.gov |
| 22 | Financial Education | EMMA |
| 23 | Facilitation Services | EMMA |
| 24 | Transportation Services | EMMA |
| 25 | Administration Program | EMMA |
| 26 | Pest Control | EMMA |
| 27 | Therapy Services | EMMA |

**Decision: REJECT (regardless of location).**

> **Note:** Rows marked "SAM.gov + EMMA" merge synonyms/near-duplicates from both source docs into one category — the alternate phrasings are kept in parentheses so the keyword-matching layer can still catch all variants. Claude Code should treat each parenthetical as an additional match string, not a separate rule.

---

## 4. Rule C — Allowed Services (US Mainland Only)

Installation, repair, and maintenance services we DO provide — but **only** when the place of performance is within the United States Mainland. The same service performed outside the US Mainland is not pursued.

| # | Allowed Service (US Mainland) |
|---|---|
| 1 | Cable Installation |
| 2 | Fence Installation |
| 3 | Furniture Installation |
| 4 | UPS / Generator Repair and Maintenance |
| 5 | IT Hardware / Software Installation and Maintenance |
| 6 | HVAC Installation, Repair and Maintenance |
| 7 | Industrial Hardware Installation |
| 8 | Roofing Installation, Repair and Maintenance |
| 9 | Door / Window Installation |
| 10 | AV Equipment Installation |
| 11 | Storage Rack and Shelving Installation |

**Decision: PURSUE only if performed in the US Mainland; otherwise REJECT.**

> ⚠️ **Critical open item:** It is **not confirmed whether EMMA bid records expose a place-of-performance / location field** at all. This entire rule is contingent on that field existing and being reliably populated in EMMA data.
> - Claude Code must inspect real EMMA bid records first to check for a location field.
> - **If no location field exists:** this rule cannot be applied as-is. Fallback options to decide on later: (a) treat all Rule C matches as PURSUE by default, (b) treat all Rule C matches as manual review, or (c) attempt geo-inference from bid text/agency address. Do not silently default — flag ambiguous cases for manual review rather than guessing.

---

## 5. Combined Decision Matrix

| Requirement Type | US Mainland | Outside US Mainland / Unknown Location |
|---|---|---|
| Hardware / material supply (Rule A) | PURSUE | PURSUE |
| Allowed service (Rule C list) | PURSUE | REJECT |
| Excluded service (Rule B list, merged) | REJECT | REJECT |
| Service not on either list | Manual review | REJECT |

*"Outside US Mainland" includes non-mainland US territories (Guam, Puerto Rico, US Virgin Islands, American Samoa, Northern Mariana Islands) and all foreign locations. For hardware these are still pursued; for services they are not.*

> If location cannot be determined for a given EMMA bid (per the open item in Section 4), treat it the same as "Outside US Mainland / Unknown" for Rule C purposes — i.e., default to REJECT or manual review, never auto-PURSUE, until the location gap is resolved.

---

## 6. Decision Flow

```
Bid Full Text
      |
      v
[ STEP 1 ] Is the requirement HARDWARE / MATERIAL?
      |-- YES --> PURSUE (any location)
      | NO (it is a service)
      v
[ STEP 2 ] Does the service match the EXCLUDED list (Rule B, merged)?
      |-- YES --> REJECT (any location)
      | NO
      v
[ STEP 3 ] Does the service match the ALLOWED list (Rule C)?
      |-- NO --> Manual review (US) / REJECT (outside US / unknown location)
      | YES
      v
[ STEP 4 ] Is place of performance US Mainland?
      |-- YES --> PURSUE
      |-- NO / UNKNOWN --> REJECT
```

---

## 7. Open Items for Claude Code to Verify Before Implementation

These are explicitly unresolved — do not assume answers, confirm against real EMMA data/portal structure first:

1. **Location field availability:** Does EMMA expose place-of-performance data per bid? If yes, what format (state, city, zip, free text)? This determines whether Rule C / Step 4 can be automated or needs a fallback.
2. **Hardware vs. service classification signal:** Does EMMA provide a structured field (e.g., NAICS/UNSPSC code, category tag) to distinguish hardware/material bids from services, or does this need to be inferred from bid title/description text via keyword/LLM classification (as is likely done for the SAM.gov pipeline)?
3. **Hardware bid volume on EMMA:** Confirm whether Rule A is materially relevant for EMMA's bid mix, or whether EMMA is effectively services-only (in which case Rule A logic can be simplified/deprioritized).
4. **Keyword matching scope:** Confirm whether matching should run against bid title only, full description, or both — and whether fuzzy/partial matching is needed for the merged Rule B terms (e.g., "Maintenance" alone appearing inside a longer allowed-service phrase like "HVAC Installation, Repair and Maintenance" should NOT trigger a false REJECT).

---

## 8. Configuration Notes for the System

To align the EMMA evaluation pipeline with this document:

1. Classify each bid first as **HARDWARE/MATERIAL vs SERVICE** — not location-first (pending Open Item #2).
2. Maintain the merged **Rule B (excluded)** and **Rule C (allowed)** lists as editable, database-backed category lists, matched against bid text — not hardcoded.
3. Apply Rule B rejections **globally**, independent of place of performance.
4. Apply the US-Mainland location check **only** to Rule C allowed services and to ambiguous/unlisted services — contingent on resolving Open Item #1.
5. Never auto-pass a bid solely because location looks fine; the requirement type must still be validated against Rules A/B/C first.
6. Watch for false-positive keyword collisions between Rule B (excluded) and Rule C (allowed) terms — e.g., "Maintenance" is excluded on its own but is embedded in several allowed-service phrases (HVAC, UPS/Generator, Roofing). Matching should be scoped to the full category phrase, not single-word substrings, to avoid an allowed service being wrongly rejected.

---

*Source of truth: merges `Company_Bid_Selection_Criteria.docx` (SAM.gov) and `EMMA_Keywords.docx` (EMMA). Supersedes any location-first evaluation logic for EMMA. Sections marked with ⚠️ are unresolved assumptions and must be confirmed against live EMMA data before the corresponding logic is built.*
