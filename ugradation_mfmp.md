# MFMP Upgrade — Proposal: Ad-Status Sweep + Niche Classification Engine

**Revision 3** — aligned to `MFMP_Niche_Classification_Criteria.md` v2.0.

**Status:** proposal only. No code has been changed. Nothing is implemented until you give the go-ahead.

**Scope guarantee:** the existing niche flow (commodity-code search and keyword search) is not modified. Every line of current behaviour in `server/app/scrapers/myflorida/scraper.py`, `router.py`, `ingest.py`, `workbook.py` and `commodity_codes.py` keeps working exactly as it does today. See [§11 Isolation guarantee](#11-isolation-guarantee).

---

## 0. Read this first — three things block the build

| # | Item | Why it blocks |
|---|---|---|
| **1** | **Cross-listing contradicts your earlier instruction** | The criteria doc §5 puts a bid in its primary lane **and** every secondary lane. You told me: *"make sure the bid will come in only one niche sheet no duplicate need in it."* These cannot both be true. §1.1 below. |
| **2** | **`mfmp_niches.yaml` does not exist** | The criteria doc calls it "single source of truth for code" and §9.2 forbids putting any lexicon, code, weight or threshold in Python. Every term list, code tier, weight and threshold lives in a file that is not in the repo. Nothing can be scored without it. §3. |
| **3** | **The MFMP Commodity Code v20 Public workbook does not exist here** | §9.6 requires validating codes against it at startup, demoting unvalidated `candidate_requires_validation` codes to Tier C. Not in the repo. §4.3. |

Everything else in this document is settled and ready to build.

---

## 1. What changed in this revision

The criteria doc replaces my strawman scoring model wholesale. Three of your earlier decisions are **confirmed** by it, one is **contradicted**.

| Your earlier decision | Criteria doc | Status |
|---|---|---|
| No Ollama / LLM | Deterministic scoring throughout | ✅ agree |
| No PURSUE/REJECT | §0: "does not decide whether a bid is worth responding to" | ✅ agree |
| No kill-words | §0: "No bid is rejected, filtered or dropped" | ✅ agree |
| **One sheet per bid, no duplicates** | **§5: cross-listed into every secondary lane** | ❌ **conflict** |

Superseded from revision 2: my `CONFIDENCE_THRESHOLD = 2.0` strawman, the `strong`/`weak` term shape, the Python `NICHES` dict, and five niches. The real model is C+T+S scoring to 100 with a threshold of 40, six niches, and YAML-driven configuration.

### 1.1 The cross-listing conflict — I need your call

Criteria doc §5:

> The bid appears **once** in its primary niche lane, `role = OWNER`. It appears in each secondary lane as `role = CROSS-LISTED` … A website redesign with branding genuinely belongs in both N4 and N1.

Your instruction to me: *"make sure the bid will come in only one niche sheet no duplicate need in it."*

Three ways to resolve it:

| Option | Behaviour | Trade-off |
|---|---|---|
| **A. Your instruction wins** | One row, primary lane only. `Secondary Niches` becomes a *column* recording what it would have been cross-listed to. | No duplicate rows. Someone working only the N1 sheet never sees the N4-primary website-redesign job that also needed branding. |
| **B. The criteria doc wins** | OWNER row in primary, CROSS-LISTED row in each secondary lane. | Matches the spec exactly. The same ad appears on 2–3 sheets, which is what you said you didn't want. |
| **C. Both, switchable** | Build option A's behaviour, with a `cross_listing: false` flag in the YAML that turns on B. | Slightly more code. Decide later with real output in front of you. |

**My recommendation: C.** The disagreement is genuinely hard to settle in the abstract — it depends on whether your reviewers work per-lane or scan the whole workbook, which one real run will show you. C costs little and makes the decision reversible. If you want a straight answer instead: **A**, because you stated it directly and recently, and the `Secondary Niches` column preserves the information without duplicating rows.

### 1.2 A related inconsistency inside the criteria doc

§9.4 states the invariant:

> `classified_count == sum(bids per niche lane) + count(OTHER)`

That invariant **only holds if lanes count OWNER rows only**. With cross-listing on, a bid that is OWNER in N4 and CROSS-LISTED in N1 contributes 2 to `sum(bids per niche lane)` and 1 to `classified_count`, so the equality breaks on any run with a single cross-listed bid.

So either the invariant means OWNER rows, or it contradicts §5. Worth settling when you answer §1.1, since the implementation asserts this invariant on every run.

---

## 2. What you asked for (unchanged)

1. Log in → Advertisements → Advanced Search. *(identical to today)*
2. Set **one** filter: **Ad Status** — Preview / Open / Closed / Withdrawn.
3. Search, take **all** records across **all pages**.
4. Open each bid, download its documents.
5. Extract document text; classify on commodity code + title + scope.
6. One workbook, one sheet per niche, plus **Other**.

Both revision-1 blockers stay resolved by the HTML you supplied — the `mat-paginator-navigation-next` button (§5.1) and `#mainSection` (§5.2).

---

## 3. Configuration: `mfmp_niches.yaml`

Criteria doc §9.2 is unambiguous:

> **No lexicon, code, weight or threshold in Python.** All of it lives in `mfmp_niches.yaml`. Adding a seventh niche must be a YAML edit and nothing else.

That is stricter than my revision-2 plan, which put a Python dict in `sweep/niches.py`. **It also means the point tables themselves are data** — the 45/25/30 weights, the 40/55/75 bands, and every row of the §3.1–3.3 scoring tables. The Python side becomes a loader plus a pure scoring function that reads its constants from the file.

The file does not exist. To score anything, it needs to carry:

**Per niche (N1–N6):**

| Key | Feeds | Notes |
|---|---|---|
| `label`, `sheet`, `order` | routing, workbook | see §8.1 on sheet names |
| `codes.tier_a` / `tier_b` / `tier_c` | C (45 / 34 / 22) | UNSPSC codes |
| `codes[].source` | §9.6 validation | `candidate_requires_validation` demotes to Tier C if unvalidated |
| `core_terms` | T (17, +4 for a second) and S | |
| `supporting_terms` | S only | |
| `umbrella_terms` | T (10 + `deep_read_required`) | |
| `exclusion_terms` | suppression (§2.1) | cancel a match, never subtract |
| `stem_map` | matching | |
| `deliverables` | S (+6) | Gerber files, wireframes, press-ready PDF, media plan, … |

**Global:**

| Key | Feeds |
|---|---|
| `high_intent_modifiers` | T (+4) — `services`, `development`, `redesign`, `campaign`, `repair`, … |
| `scoring.*` | every point value and cap in §3.1–3.3 |
| `thresholds` | 40 match, 55 secondary, 20 secondary gap, 8 contested, 75/55/40 strength bands |
| `routing.hard_primary_override` | N6 override vocabulary (§5.1) — `pcb`, `circuit board`, `schematic`, `gerber`, `solder`, `controller board`, `vfd`, `bga`, `smd`, … |
| `tie_breaks` | §7 below |
| `cross_listing` | §1.1 option C, if you take it |

I can draft this file from the criteria doc's prose as a starting point for you to correct — the doc names enough terms to seed it. **Say if you want that**; otherwise I wait for yours.

---

## 4. The scoring model, and what the arithmetic implies

`niche_score = C + T + S`, max 100, threshold 40. I worked through the number ranges; four consequences are worth knowing before the lexicons are written, because they change what the pipeline must get right.

### 4.1 For uncoded ads, scope text is load-bearing — not optional

The neutral-15 rule (no code published → C = 15) interacts with the threshold like this:

| Situation | C | T | S | Total | Classified? |
|---|---|---|---|---|---|
| Uncoded, one core title term | 15 | 17 | 0 | **32** | ✗ below 40 |
| Uncoded, one core term + modifier | 15 | 21 | 0 | **36** | ✗ below 40 |
| Uncoded, perfect title, no scope | 15 | 25 | 0 | **40** | ✓ exactly at threshold |
| Uncoded, one core term + one scope term | 15 | 17 | 8 | **40** | ✓ exactly at threshold |

An uncoded ad with a single core term in its title **does not classify on the title alone**. It needs scope evidence to cross 40.

This is the strongest possible argument for the `#mainSection` extraction and the attachment-text pipeline. If scope text were dropped to save runtime, every uncoded single-term ad would fall into Other, and the criteria doc notes uncoded ads are "a large share" of MFMP. **Recall depends on the document pipeline working.**

### 4.2 An uncoded ad can never be STRONG

Max uncoded score = 15 + 25 + 30 = **70**, and STRONG starts at 75. So no ad without a commodity code can ever be labelled STRONG.

That is consistent with §4's own definition — "Code and text agree" — so I read it as intentional rather than a defect. Flagging it because it means **match-strength distributions will look pessimistic** if MFMP ads are mostly uncoded: a large PROBABLE band and an empty-ish STRONG band would be the model working correctly, not a tuning failure. Worth knowing before you read the first run's output.

### 4.3 A Tier A code classifies a bid with zero text support

C = 45 alone clears the 40 threshold. An ad whose published code is Tier A for N3 lands in N3 even if its title and scope produce nothing.

The doc's design handles this well: 45 falls in the **POSSIBLE** band (40–54), so such a bid is explicitly marked as thin evidence rather than presented as a confident match. No change needed — noting it so the POSSIBLE band's population makes sense when you see it.

The real exposure is that **45 of 100 points ride on code quality**, which makes §9.6's validation step and the missing v20 workbook (§0 item 3) more load-bearing than a startup check usually is.

### 4.4 A miscoded ad is scored worse than an uncoded one — worth a decision

The C table treats three cases differently:

| Published code | C |
|---|---|
| None at all | **15** (neutral) |
| Tier A for a *different* niche, and this niche's text fires | **10** |
| Unrelated to all six niches | **0** |

So an ad tagged with, say, a janitorial code whose title reads "Website Redesign Services" scores C = 0 for N4 and needs T + S ≥ 40 — where the *same ad with no code at all* would start from 15.

The tension: the doc's own §3.1 justifies the neutral-15 rule by warning that "agencies classify similar work under different codes." A miscoded ad is precisely that failure mode, and the C = 0 row penalises it rather than treating it as absent information. The C = 10 row covers only codes that are Tier A *for another one of your six niches* — not codes outside your taxonomy entirely, which is the common miscoding case.

**Question for you:** should "unrelated to all six niches" score 15 (no information) instead of 0 (negative evidence)? I have not changed anything — this is your spec and your call. I'm flagging it because it will show up as unexplained Other-lane entries whose titles obviously belong to a niche, and it would be easy to misdiagnose that as a lexicon gap during tuning.

### 4.5 Where do commodity codes actually come from?

C is the largest single signal, so its input source is the highest-risk unknown in the whole design. Two candidates, neither confirmed:

1. **The portal's Excel export.** `ingest.py:40` already maps candidate headers `commoditycode / commoditycodes / commodity / nigp / unspsc` — **if the export contains such a column.** Unverified.
2. **Regex over `#mainSection`.** Your sample description carries them in a readable block:
   ```
   Commodities:
   43211500   Computers
   43233004   Operating System Software
   ```
   An `\b\d{8}\b` scan over the description recovers these, plus the trailing title text for auditability.

**Plan: use the export column as primary, regex over the description as fallback, and record which source fired** in the `matched_codes` field so a systematic failure is visible rather than silent. If neither yields codes, 45 points of the model goes dark and everything runs through the neutral-15 path — survivable per §4.1, but it makes the lexicons carry the entire load.

Phase 0 answers this with one export and one bid.

---

## 5. Portal mechanics (unchanged from revision 2, condensed)

### 5.1 Pagination

```html
<button class="… mat-paginator-navigation-next …" aria-label="Next page">
```

Standard Angular Material paginator. Loop until the next button carries `disabled` / `mat-button-disabled`. A sibling `.mat-paginator-range-label` normally reads `"1 – 100 of 4523"`, giving a true total and real progress reporting — unconfirmed, checked in Phase 0, not depended on.

Selectors anchor on `aria-label` and the Material class, **never** `_ngcontent-foe-c285`, which changes on every portal deploy.

**Prior art:** `server/app/scrapers/wisconsin/scraper.py:313-325` already does exactly this — range read, `_go_next_page()`, `MAX_PAGES` guard, and a stall detector that breaks when the range stops advancing. I'll mirror it rather than invent one. Nothing else in this codebase paginates.

**The problem pagination introduces.** `process_bid` returns to the list with `driver.back()` (`scraper.py:544`) and finds bids by visible link text (`scraper.py:517`), because the Number cell has no href — only a JS handler (`scraper.py:508-510`). Material keeps its page index in component memory, not the URL, so returning from a bid opened on page 4 may re-render at page 1, making the rest of page 4 unreachable. Silently.

Handling, in order of preference:

1. **Capture the detail URL and navigate directly.** `process_bid` already waits on `"/detail/" in current_url`, so a real URL exists once you are there. If it is directly reachable, collect every row across every page first, then visit bids by URL — no pagination dance at all. Phase 0 verifies.
2. **Page-at-a-time with position restore.** Process page *N*, and after each `back()` re-read the range label, clicking Next to restore position if it reset. Correct, O(pages²) clicks.
3. **New tab per bid.** Leaves the grid untouched; costs a tab lifecycle per bid.

The bid-visiting step is designed to be swappable between 1 and 2 without restructuring anything else.

**Does Export cover all pages?** Unknown, and it is the source of every metadata column. If per-page, `workbook.merge_exports` already merges multiple exports de-duplicated by ad number. Phase 0 answers it with one click.

### 5.2 Description

`(By.ID, "mainSection")`, read via `element.text`.

The markup nests `<p>` inside `<p>`, which is invalid HTML — the browser re-parses and flattens it, so the live DOM does not match the source string. Reading rendered text sidesteps that and turns `&nbsp;` into ordinary spaces. Best-effort: a missing `#mainSection` records a warning, leaves the `Description` column blank, and the bid is scored on title + documents with S computed from whatever text exists.

---

## 6. Module layout

```
server/app/scrapers/myflorida/
├── scraper.py, router.py, ingest.py, workbook.py, commodity_codes.py   ← ALL UNTOUCHED
└── sweep/                          ← NEW
    ├── __init__.py
    ├── mfmp_niches.yaml            ← YOUR config; single source of truth (§3)
    ├── config.py                   YAML loader + startup validation (§9.6)
    ├── scraper.py                  SweepScraper(MFMPScraper) — overrides + pagination
    ├── codes.py                    code extraction (export column → description regex)
    ├── scoring.py                  C / T / S — pure, no constants of its own
    ├── matching.py                 whole-word, phrase-aware, stem_map, exclusion suppression
    ├── routing.py                  N6 override, argmax, secondary, contested, tie-breaks
    ├── documents.py                download → extract text → delete
    ├── workbook.py                 multi-sheet writer
    ├── models.py                   mfmp_sweep_bids + mfmp_sweep_scores
    ├── export.py                   DB persistence + workbook rebuild
    └── router.py                   /myflorida/sweep/* endpoints
```

Run key: **`myflorida_sweep`**, distinct from `myflorida`.

`scoring.py` holding no constants is what makes §9.2 enforceable rather than aspirational — a weight in Python would be a review-catchable bug, not a style preference.

---

## 7. Tie-breaks (§6 of the criteria doc)

Most are mechanizable from term lists; two are not. Sorting them honestly matters, because a rule that reads as automatic but needs judgment will silently pick wrong.

| Pair | Mechanizable? | How |
|---|---|---|
| N1 vs N2 | ✅ | Two term lists: *assets* (logo, layout, template) vs *audience outcome* (reach, impressions, media buy, campaign management) |
| N1 vs N3 | ✅ | Print-spec vocabulary — quantities, paper stock, trim size, binding, delivery locations. "Quantities dominant" = count of print-spec hits vs design hits |
| N2 vs N3 | ✅ | Targeting/strategy terms vs print-and-mail quantity terms |
| N1 vs N4 | ✅ | `deliverables` lists already separate *working system* from *mockups / wireframes / style guide only* |
| N4 vs N5 | ⚠️ **partly** | Term lists resolve the ordinary case (models/training/inference/LLM/NLP → N5; CRUD/portal/forms/CMS → N4). **"AI-powered portal → N4 if the portal is the deliverable, N5 if the model is" needs semantic judgment no keyword list provides.** |
| N4 vs N5 (BI) | ✅ | Fully deterministic — dashboard on existing data → N4 *unless* `43232314` or `80101508` is published → N5 |
| N6 vs any | ✅ | §5.1 hard override, applied before argmax |

**Proposal for the N4/N5 deliverable-ambiguity case:** when both sides fire and neither dominates, do not guess — set `contested = true` and let the primary fall to the higher score. That is exactly what the `contested` flag exists for (§5), and it converts a silent wrong answer into a visible one. The alternative is a heuristic ("whichever noun is closer to the front of the title") that will be wrong often enough to erode trust in the whole lane.

The tie-break term lists belong in the YAML under `tie_breaks`, per §9.2.

---

## 8. The output workbook

### 8.1 Sheet names — four of your six are illegal as written

Excel caps sheet names at **31 characters** and forbids `: \ / ? * [ ]`. Checking your six:

| ID | Niche name | Length | Legal? |
|---|---|---|---|
| N1 | Graphic Design & Creative Services | 34 | ✗ too long |
| N2 | Digital Marketing, Advertising & Outreach | 41 | ✗ too long |
| N3 | Printing & Print Production | 27 | ✓ |
| N4 | Software, Web & UI/UX Development | 33 | ✗ too long **and contains `/`** |
| N5 | AI, Data & Automation | 21 | ✓ |
| N6 | PCB & Electronics Engineering Services | 38 | ✗ too long |

Proposed tabs — ID-prefixed so sheet order is self-evident and a reviewer can cite "N4" without ambiguity:

```
N1 Graphic Design      N4 Software & Web
N2 Digital Marketing   N5 AI & Data
N3 Printing            N6 PCB & Electronics       Other
```

All ≤ 20 characters, no illegal characters. **Override these with your own names if you prefer** — they go in the YAML's `sheet` key.

### 8.2 Columns

The criteria doc §7 defines a 15-field result object and explicitly stops there ("This spec does not define storage"), so the sheet layout is this plan's job. Per niche sheet:

| Group | Columns |
|---|---|
| **Identity** (portal export) | Ad Number, Title, Agency, Ad Type, Status, Ad Date, Open Date, Close Date |
| **This niche's verdict** | Role *(OWNER / CROSS-LISTED, if §1.1 keeps it)*, Match Strength, Score, C, T, S |
| **Full picture** | N1…N6 Score (6 columns), Primary Niche, Secondary Niches |
| **Explainability** | Matched Codes *(+ tier + source)*, Matched Keywords *(+ field)*, Deliverables Detected, Suppressed Terms, Flags |
| **Provenance** | Description *(truncated)*, Documents, Document Text *(chars)* |

The **Other** sheet adds `Other Reason`, `Closest Niche`, `Closest Niche Score`.

Two deliberate choices:

- **All six scores on every row.** Criteria doc §5.2 wants a threshold change replayable over history without re-fetching. Six columns on every row makes that a spreadsheet filter rather than a re-run.
- **Per-niche C/T/S only for the sheet's own niche.** The full 6 × C/T/S breakdown is 18 columns and would drown the sheet; it goes to `mfmp_sweep_scores` in the DB, where replay queries actually want it.

`Document Text` = characters extracted. `0` flags a bid judged without attachment evidence — a scanned-image PDF yields nothing, and without this column that is indistinguishable from a bid with 40 pages of scope.

Excel's 32,767-character cell limit forces the Description truncation; the full text lives in the DB.

**Empty sheets** are still written with headers, so the workbook shape is stable run to run.

**Styling** follows SAM's convention (`sam/export.py:30-36`): navy header row, auto-fit widths, illegal control characters stripped.

---

## 9. Tuning (criteria doc §8) — a feature this plan does not yet contain

§8 opens with: *"The lexicons will be wrong on day one. The feedback path is not optional."* It then requires:

1. Human "misclassified" marking, recording the terms that caused it.
2. Human "promote out of Other", recording the terms that should have fired.
3. A periodic report: per-niche precision/recall against a human-labelled set; top unmatched terms in promoted bids; top terms in misclassifications; codes seen in postings but absent from the YAML; score distribution in Other by `closest_niche`; codes on ads scoring high on N6 text.
4. Periodic re-run of the **Closed** search to re-classify historical awards.

That is a **review UI, a human-labels table, and a reporting job** — comparable in size to the classifier itself. My revision-2 plan had none of it, and I am not going to pretend a `Matched Keywords` column satisfies it.

Item 4 is nearly free and aligns neatly: **Closed is one of your four Ad Status options**, so a recall test is just another sweep run with a different status.

**Recommendation: build the classifier first (Phases 0–5), then decide on tuning as a separate piece of work** once you have seen real output and know whether the lexicons need heavy iteration. The DB schema will carry `mfmp_sweep_scores` from day one so no history is lost in the meantime — that is the one thing that would be expensive to retrofit.

**Say if you'd rather have the tuning loop in scope from the start.** It roughly doubles the work.

---

## 10. Delivery, API, UI

**Delivery.** Documents are deleted after extraction, so a run produces exactly one file — same as SAM. `myflorida_sweep` joins `EXCEL_ONLY_PORTALS` in `app/core/exports.py`; the run archives, downloads and emails as a bare `.xlsx`. The niche flow keeps its ZIP.

**API** — new router, existing one untouched:

| Endpoint | Purpose |
|---|---|
| `GET /myflorida/sweep/niches` | niche catalogue from the YAML, for the UI |
| `POST /myflorida/sweep/scrape` | body `{ ad_statuses: ["open"] }`; `?live_preview=` supported |
| `GET /myflorida/sweep/scrape/status/{run_id}` | poll |
| `GET /myflorida/sweep/scrape/runs` | history |

**Progress.** With pagination there is a real denominator. If `.mat-paginator-range-label` exists, `RunStatus` shows `"page 3 of 46 · 218 bids collected"`; otherwise `"page 3"`.

**UI placement.** A **third mode inside the existing MyFlorida panel**, beside Codes / Keywords: "Full sweep", hiding the niche picker and showing the four Ad Status checkboxes. One console page, one mental model. Alternative is a separate sidebar tile, more discoverable but implies MyFlorida is two sources. *Recommendation: third mode. Still awaiting your preference.*

---

## 11. Isolation guarantee

- **`sweep/scraper.py` subclasses `MFMPScraper` and overrides three methods.** Subclassing adds behaviour without editing the parent, so the existing code path is byte-identical.
- **No shared mutable state.** Different run key, different tables, different router, different workbook writer.
- **Pagination lives only in the sweep.** The niche flow keeps its single-page assumption — it never reaches 100 results anyway.
- **Trade-off I'll keep flagging rather than bury:** subclassing means a future fix to `MFMPScraper.login` or `submit_search` affects both flows. Usually a feature; still real coupling. Full isolation means copying ~300 lines that then drift. *Recommendation: subclass.*

Pre-existing files that change at all:

| File | Change |
|---|---|
| `server/main.py` | one `include_router` line |
| `server/app/core/exports.py` | add `"myflorida_sweep"` to `EXCEL_ONLY_PORTALS` |
| `server/app/scrapers/sam/engine/text_extractor.py` | **only if** you pick the §12 promotion — a file move plus a re-export |
| `client/src/lib/runs.ts` | mirror the excel-only set |
| `client/src/components/MyFloridaPanel.tsx` | the third-mode toggle |
| `client/src/lib/api.ts` | new client functions |

Additive only. No existing behaviour altered.

---

## 12. Text extraction

`server/app/scrapers/sam/engine/text_extractor.py` already does the job: `build_full_text(description, docs_folder)` walks a folder, extracts `.pdf` via PyMuPDF and `.docx` via python-docx (with old-binary-`.doc` detection), reads `.txt`, skips the rest, and returns one string with each file under a `=== filename ===` heading. Nothing in it is SAM-specific.

Either **import it from `sam/engine/`** (zero changes, MyFlorida depends on SAM's layout) or **promote it to `app/core/text_extractor.py`** (pure file move, SAM keeps working via a re-export). *Recommendation: promote — a second consumer is when shared infrastructure should stop living inside one portal's engine.* Still awaiting your preference.

Per-bid folders are deleted after extraction, matching `sam_scraper.py:643`.

---

## 13. Build order

**Status as of revision 3 build:** phases 1–4 are implemented and verified offline.
Phase 0 (the live probe) and phase 5 (tuning) both need portal access and are
outstanding. Built on the recommended defaults from §15 — `cross_listing: false`,
SAM's text extractor imported rather than promoted, third mode in the MyFlorida
panel, optional bid cap present and defaulting to unlimited. Each is a one-line
change if you decide differently.

| Phase | Work | Output |
|---|---|---|
| **0** | **Probe run** — sweep by status, page through, export, open one bid. Answers: (a) `.mat-paginator-range-label` present with a total? (b) is `/detail/` directly addressable (§5.1 option 1)? (c) does Export cover all pages or one? (d) **does the export carry a commodity-code column (§4.5)?** (e) how many records does an Open sweep return? | Findings note. **No production code.** |
| **1** | `mfmp_niches.yaml` + loader + `matching.py` + `scoring.py` + `routing.py`, with unit tests over hand-written ads covering every C/T/S row, the N6 override, and each tie-break | Classifier testable with no browser and no portal |
| 2 | `sweep/scraper.py` pagination + `#mainSection` + `codes.py` + `documents.py` | Full coverage, text and codes per bid |
| 3 | `sweep/workbook.py` + `export.py` + `models.py` | The multi-sheet workbook |
| 4 | Router, delivery wiring, UI mode | End-to-end |
| 5 | Live run; tune lexicons and thresholds against real output | Tuned |
| *(6)* | *Tuning loop per §9 — only if you scope it in* | Review UI, labels table, report |

Phase 1 is testable in complete isolation from the portal, which is the main reason to keep the scoring functions pure and constant-free. Phase 0 stays cheap and answers five questions that are expensive to discover in Phase 2.

---

## 14. Risks

| Risk | Severity | Handling |
|---|---|---|
| **No commodity codes available from either source** | **High** | 45 of 100 points goes dark; everything runs the neutral-15 path and lexicons carry the load. §4.5 — Phase 0 (d) |
| `back()` resets the paginator, silently skipping bids | **High** | §5.1 — probe for direct detail URLs; position-restore fallback |
| Runtime — an unfiltered sweep parses documents for every ad | **High** | Now the dominant cost. Stop button works; suggest an optional per-run bid cap for trial runs |
| Lexicons wrong on day one | **High**, expected | The criteria doc says so itself (§8). Phase 5, and §9 if you scope it in |
| Miscoded ads penalised vs uncoded (§4.4) | Medium | Your decision — surfaces as unexplained Other entries |
| Threshold 40 mis-set for real data | Medium | All six scores stored per bid, so replay needs no re-fetch |
| N6's code list is unvalidated | Medium | Criteria doc flags it; §9.6 demotes to Tier C. N6 leans on keywords |
| Export covers only the visible page | Low | `merge_exports` already handles multi-export merging |
| Scanned PDFs yield no text | Low | Visible via `Document Text`; OCR out of scope |
| Portal deploy changes Angular attributes | Low | Selectors anchored on IDs, `aria-label`, Material classes — never `_ngcontent-*` |

---

## 15. Still needed from you

**Blocking:**

1. **§1.1 — cross-listing.** One row per bid (your instruction), cross-listed rows (the spec), or switchable? *(recommend: switchable, defaulting to one row)*
2. **§1.2 — does the §9.4 invariant count OWNER rows only?**
3. **`mfmp_niches.yaml`.** Every lexicon, code tier, weight and threshold. *Offer: I can draft it from the criteria doc's prose for you to correct.*
4. **The MFMP Commodity Code v20 Public workbook**, for the §9.6 startup validation.

**Decisions on your own spec:**

5. **§4.4** — should a code unrelated to all six niches score 15 (no information) rather than 0 (negative evidence)?
6. **§8.1** — accept my proposed sheet tab names, or supply your own?
7. **§9** — tuning loop in scope now, or after the classifier ships? *(recommend: after)*

**Non-blocking preferences** (I'll proceed on the defaults):

8. §12 — import SAM's text extractor, or promote it to `app/core/`? *(recommend: promote)*
9. §10 — third mode in the MyFlorida panel, or separate sidebar tile? *(recommend: third mode)*
10. Optional per-run bid cap for trial runs? *(recommend: yes, default unlimited)*

Nothing gets written until you say go.

---

## 16. Unrelated pre-flight note

`server/migrations/2026-07-28_add_sam_ollama_columns.sql` has still never been applied to your database — `sam_bids` is missing all three Ollama columns, so every SAM run fails its DB save and silently falls back to an in-memory sheet. That is why `sam_bids` has 0 rows.

It does not affect MyFlorida: `mfmp_bids` has its `matched_keyword` column, so the July 16 migration was applied. Noted because the new sweep tables will want the same "did the migration actually run" check.
