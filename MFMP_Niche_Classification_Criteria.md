# MFMP Niche Classification Criteria
### How a MyFloridaMarketPlace advertisement is sorted into one of six niches, or into Other
**Version:** 2.0 · **Prepared:** July 2026 · **Owner:** Rizviz International Impex — Bid Sourcing
**Companion machine-readable config:** `mfmp_niches.yaml` (single source of truth for code)

---

## 0. Scope of this document

MFMP publishes every State of Florida advertisement into one flat stream. This document answers
exactly one question about each advertisement:

> **Which of our six niches does this bid belong to?**

Nothing else. It does not decide whether a bid is worth responding to, whether the deadline is
workable, whether the value is right, or whether we can deliver it. Those are **pursuit** questions
and they belong to a separate layer that can be added later. Mixing them in here is what makes a
classifier produce a single ranked list instead of six clean piles.

The output is a **scatter**: each bid lands in the niche lane it belongs to, and anything that
belongs to none of them lands in **Other**. No bid is rejected, filtered or dropped.

Two rules carry the whole standard:

- **Every bid is scored against all six niches independently**, never assigned to the first one that
  matches. This is what stops everything piling into "Software".
- **Every bid ends somewhere.** One of `N1`–`N6`, or `OTHER`.

---

## 1. The six niches

| ID | Niche | What identifies it |
|----|-------|--------------------|
| `N1` | Graphic Design & Creative Services | Brand systems, layout, publication and exhibit design, illustration, visual identity |
| `N2` | Digital Marketing, Advertising & Outreach | Campaigns, social and paid media, public information/outreach, media planning and buying, SEO |
| `N3` | Printing & Print Production | Offset/digital printing, publication and promotional print, direct mail, large format, fulfilment |
| `N4` | Software, Web & UI/UX Development | Custom applications, web and portal builds, systems integration, UI/UX, QA, cloud |
| `N5` | AI, Data & Automation | GenAI/LLM, machine learning, NLP/chatbots, document processing, BI and data platforms, RPA |
| `N6` | PCB & Electronics Engineering Services | PCB design and layout, schematics, prototyping, reverse engineering, failure analysis, board repair |

> **N6 code coverage is weak.** The PCB source document is keyword-only and carries no MFMP commodity
> codes. Codes proposed for N6 in `mfmp_niches.yaml` are marked `source: candidate_requires_validation`
> and must be checked against the *MFMP Commodity Code v20 Public* workbook before being trusted. N6
> therefore leans on keyword evidence far more than the other five.

---

## 2. What counts as niche evidence

Three signals, and only three. Each niche defines its own version of all three in the YAML.

| Signal | Source field | Why it is evidence of a niche |
|--------|--------------|-------------------------------|
| **Commodity code** | Published UNSPSC code(s) on the ad | The agency's own classification of what it is buying |
| **Title** | Advertisement title | The strongest short-form statement of the work |
| **Scope** | Description, scope of work, extracted attachment text | Where the actual deliverables are named |

### 2.1 Exclusion terms are suppressors, not penalties

Each niche carries `exclusion_terms`. These do not subtract points — they **cancel a match that would
otherwise fire**, because the term means something else in that context.

- "3D printing" and "managed print services" must not count as a `printing` hit for N3.
- "landscape design" and "instructional design" must not count as a `design` hit for N1.
- "building electrical" and "electrical contractor" must not count as an electronics hit for N6.

Rule: when a core or supporting term appears **inside** an exclusion phrase, that occurrence does not
count as a match. If every match for a niche is suppressed, that niche scores 0 on Title and Scope.

---

## 3. Niche relevance score (0–100, computed per niche)

`niche_score = C + T + S`

| Signal | Max | Rationale for the weight |
|--------|-----|--------------------------|
| **C** — Commodity code alignment | **45** | The agency's own classification is the single most reliable niche indicator when present |
| **T** — Title signal | **25** | Short, dense, deliberately descriptive |
| **S** — Scope signal | **30** | Most informative when present, but noisiest and longest |

### 3.1 C — Commodity code alignment (0–45)

| Condition | Points |
|-----------|--------|
| Published code is a **Tier A** code for this niche | 45 |
| Published code is a **Tier B** code | 34 |
| Published code is **Tier C**, or shares its first 6 digits with a Tier A code | 22 |
| Published code is Tier A for a *different* niche, but this niche's title/scope signals fire | 10 |
| **No commodity code published on the advertisement** | **15** (neutral) |
| Published code is unrelated to all six niches | 0 |

The neutral-15 rule is load-bearing. A large share of MFMP ads publish one code or none, and the
company guide warns that agencies classify similar work under different codes. Scoring an uncoded bid
at zero would push real opportunities into Other on a technicality. Fifteen points is not enough to
classify a bid on its own — it needs title or scope evidence to clear the threshold — but it does not
actively block classification either.

Where multiple codes are published, take the **best-scoring** code for the niche being evaluated.

### 3.2 T — Title signal (0–25)

| Condition | Points |
|-----------|--------|
| One `core_terms` hit | 17 |
| A second distinct `core_terms` hit | +4 |
| A `high_intent_modifier` present (`services`, `development`, `redesign`, `campaign`, `repair`, …) | +4 |
| Only an `umbrella_terms` hit (`modernization`, `communications support`, `professional services`) | 10, and set `deep_read_required` |
| No hit | 0 |

Cap 25. Matching is case-insensitive, whole-word, phrase-aware, with the niche `stem_map` applied and
`exclusion_terms` suppression from §2.1.

### 3.3 S — Scope signal (0–30)

| Distinct niche terms found (core + supporting) | Base |
|---|---|
| 0 | 0 |
| 1 | 8 |
| 2 | 15 |
| 3 | 22 |
| 4 or more | 26 |

Plus up to **+6** where the scope names an artefact from the niche's `deliverables` list — Gerber
files, wireframes, brand standards manual, press-ready PDF, media plan, trained model, prototype
board. Naming the artefact is the clearest niche signal a scope can give. Cap 30.

---

## 4. Match strength

A label on *how confident the classification is*, not on whether the bid is worth pursuing.

| Label | Score | Reading |
|-------|-------|---------|
| **STRONG** | 75–100 | Code and text agree; this is unambiguously that niche |
| **PROBABLE** | 55–74 | Clear text evidence, or a good code with partial text support |
| **POSSIBLE** | 40–54 | Thin but real evidence — worth a human glance |
| *(no match)* | < 40 | Not evidence of this niche |

**Niche-match threshold = 40.**

---

## 5. Routing

Once all six scores exist:

```
if max(scores) < 40:
    primary_niche  = OTHER
    closest_niche  = argmax(scores)        # recorded for tuning, not treated as a match
    other_reason   = NO_NICHE_MATCH
else:
    primary_niche    = argmax(scores)
    secondary_niches = [n for n in niches
                        if n != primary
                        and scores[n] >= 55
                        and (scores[primary] - scores[n]) <= 20]
    contested        = (scores[primary] - second_highest) <= 8
```

- The bid appears **once** in its primary niche lane, `role = OWNER`.
- It appears in each secondary lane as `role = CROSS-LISTED`, carrying the primary niche and both
  scores, so nobody re-reviews it blind. A website redesign with branding genuinely belongs in both
  N4 and N1.
- `contested = true` marks it for a human call on which niche owns it.

### 5.1 N6 hard override

Any vocabulary from `routing.hard_primary_override` — `pcb`, `circuit board`, `schematic`, `gerber`,
`solder`, `controller board`, `vfd`, `bga`, `smd` and the rest — in title or scope gives **N6 the
primary slot outright**, even when N4 scores higher. An electronics scope filed under Software is the
most damaging classification error this engine can make, because nobody reviewing the software lane
will recognise it.

### 5.2 The Other lane

`OTHER` holds everything that matched no niche above 40. It is a destination, not a bin. Every record
in it carries:

| Field | Why it matters |
|-------|----------------|
| `other_reason` | Currently always `NO_NICHE_MATCH` |
| `closest_niche` + its score | The best tuning signal available — a cluster of 35s all pointing at one niche means a lexicon gap, not bad luck |
| All six scores | So a threshold change can be replayed over history without re-fetching |
| `matched_keywords` | Shows what nearly fired, or that nothing did |

Someone scanning Other should be able to spot a misclassified bid in seconds and promote it.

---

## 6. Tie-breaks

Applied before contested marking, to resolve genuine niche ambiguity.

| Pair | Rule |
|------|------|
| **N1 vs N2** | Deliverable is *assets* (logos, layouts, templates) → N1. Deliverable is *audience outcome* (reach, impressions, media buy, campaign management) → N2. |
| **N1 vs N3** | Scope asks for *file creation / design* → N1. Scope carries *quantities, paper stock, trim size, binding, delivery locations* → N3. Both, with quantities dominant → N3 primary, N1 cross-listed. |
| **N2 vs N3** | Targeting and strategy are scored → N2. A print-and-mail quantity buy → N3. |
| **N1 vs N4** | Output is a *working system* → N4. Output is *mockups, wireframes, style guide only* → N1. UI/UX with a build → N4 primary, N1 cross-listed. |
| **N4 vs N5** | Scope names *models, training, inference, LLM, prediction, classification, NLP, computer vision* → N5. CRUD, portal, forms, workflow, CMS → N4. "AI-powered portal" → N4 if the portal is the deliverable, N5 if the model is. |
| **N4 vs N5 (BI)** | A dashboard built on existing data → N4, unless BI/analytics codes (`43232314`, `80101508`) are published → then N5. |
| **N6 vs any** | See §5.1 — N6 wins outright. |

---

## 7. What classification hands off

Storage, file format and sheet layout belong to the implementation plan, not here. This stage produces
one result object per advertisement:

| Field | Notes |
|-------|-------|
| `advertisement_number` | Identity key |
| `scores` | All six niche scores, always present, including for OTHER |
| `primary_niche` | `N1`–`N6` or `OTHER` |
| `secondary_niches` | List of `(niche_id, score)`; empty for OTHER |
| `role` | `OWNER` in the primary lane, `CROSS-LISTED` in each secondary lane |
| `match_strength` | `STRONG` / `PROBABLE` / `POSSIBLE`; null for OTHER |
| `other_reason`, `closest_niche`, `closest_niche_score` | Set only when `primary_niche = OTHER` |
| `signal_breakdown` | C, T, S per niche |
| `matched_codes` | Which codes fired, and at which tier |
| `matched_keywords` | Which terms fired, and in which field |
| `deliverables_detected` | Artefacts named in the scope |
| `suppressed_terms` | Matches cancelled by exclusion terms — essential for debugging a wrong classification |
| `flags` | `deep_read_required`, `contested`, `n6_override_applied` |

`matched_keywords` and `suppressed_terms` are what make a lane reviewable: a person should see why a
row is where it is without opening the solicitation.

---

## 8. Tuning

The lexicons will be wrong on day one. The feedback path is not optional:

1. A bid a human marks **misclassified** records the terms that caused it.
2. A bid a human **promotes out of Other** records the terms that should have fired.
3. The periodic report covers: per-niche precision and recall against the human-labelled set; top
   unmatched terms in promoted bids (lexicon gaps); top terms in misclassifications (demotion
   candidates); commodity codes seen in real postings but absent from the YAML; **score distribution
   in Other by `closest_niche`**; and codes seen on ads scoring high on N6 text, to grow that code
   list from real postings rather than guesses.
4. Periodically re-run the **Closed** search per the company guide and re-classify historical awards.
   Cheapest recall test available.

---

## 9. Rules the implementation must not break

1. **Classification only.** No deadline, value, executability, certification, bonding or incumbency
   logic in this layer. If it answers "should we bid", it does not belong here.
2. **No lexicon, code, weight or threshold in Python.** All of it lives in `mfmp_niches.yaml`. Adding
   a seventh niche must be a YAML edit and nothing else.
3. **Score all six niches always.** No early exit on first match.
4. **Nothing is rejected.** Every advertisement gets `primary_niche` ∈ {N1…N6, OTHER}. The invariant
   `classified_count == sum(bids per niche lane) + count(OTHER)` must hold on every run.
5. **Every classification is explainable** — signal breakdown, matched codes, matched keywords with
   their field, and suppressed terms.
6. **Validate commodity codes on startup** against the MFMP v20 workbook. Any code marked
   `candidate_requires_validation` that is not found logs a warning and demotes to Tier C.
7. **This spec does not define storage.** It produces the §7 result object and stops.
