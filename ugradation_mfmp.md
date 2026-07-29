# MFMP Upgrade — Proposal: Ad-Status Sweep + Niche Evaluation Wall

**Revision 2** — updated after your review. Both blockers from revision 1 are resolved by the HTML you supplied, and your decisions are folded in.

**Status:** proposal only. No code has been changed. Nothing is implemented until you give the go-ahead.

**Scope guarantee:** the existing niche flow (commodity-code search and keyword search) is not modified. Every line of current behaviour in `server/app/scrapers/myflorida/scraper.py`, `router.py`, `ingest.py`, `workbook.py` and `commodity_codes.py` keeps working exactly as it does today. See [§10 Isolation guarantee](#10-isolation-guarantee).

---

## 1. What you asked for

A second, independent way to run MyFlorida:

1. Log in → Advertisements → Advanced Search. *(identical to today)*
2. Set **one** filter only: **Ad Status**, chosen from Preview / Open / Closed / Withdrawn. No commodity codes, no keyword, no ad type.
3. Search, and take **all** returned records across **all pages**.
4. Scrape the same fields, open each bid, download its documents. *(identical to today)*
5. **New:** instead of keeping documents in folders, run them through an evaluation wall like SAM's — extract text from every document, then evaluate on **title + description + document text**.
6. **New:** one Excel workbook, **one sheet per niche**, plus a sheet named **Other** for bids matching no niche.

### Your decisions, recorded

| Decision | Setting |
|---|---|
| Ollama / LLM in v1 | **No.** Deterministic scoring only. |
| A bid on multiple sheets | **No.** Exactly one sheet per bid, no duplication. |
| PURSUE / REJECT verdict | **Not wanted.** Classification only. |
| Niche source | **Fresh niches from you.** Do *not* reuse `commodity_codes.py`. |
| Kill-words | **None.** Every bid gets classified. |

---

## 2. Both revision-1 blockers are resolved

### 2.1 Pagination exists — the 100-record cap is not a cap

Revision 1 called the missing paginator the plan's biggest risk. You checked manually and found the control. From the HTML you sent:

```html
<button mat-icon-button type="button"
        class="mat-focus-indicator mat-tooltip-trigger mat-paginator-navigation-next
               mat-icon-button mat-button-base"
        aria-label="Next page">
```

That is a standard Angular Material paginator. **This removes the truncation problem entirely** — no date slicing, no ad-type looping, no accepting an incomplete workbook. A sweep can walk the full result set. Options A/B/C from revision 1 are withdrawn.

Two properties of Material's paginator the implementation leans on:

- **The next button carries `disabled` and the class `mat-button-disabled` on the last page.** That is the loop's terminating condition — no guessing.
- **A sibling `.mat-paginator-range-label` normally reads `"1 – 100 of 4523"`.** If present, that gives a true total up front, which is far more reliable than the current spinner heuristic and lets the UI show real progress ("page 3 of 46"). You sent only the button, so I have not confirmed the label — Phase 0 checks it. The loop does not depend on it.

**Selector plan** — anchored on `aria-label` and the paginator class, never on `_ngcontent-foe-c285`, which is a per-build Angular attribute that changes on every portal deploy:

```python
"paginator_next":  (By.CSS_SELECTOR, "button.mat-paginator-navigation-next"),
"paginator_range": (By.CSS_SELECTOR, ".mat-paginator-range-label"),
```

**Pattern to follow:** `server/app/scrapers/wisconsin/scraper.py:313-325` already does exactly this job against a comparable Angular/PeopleSoft grid — a range-indicator read, a `_go_next_page()`, a `MAX_PAGES` safety cap, and a stall detector that breaks when the range stops advancing. I'll mirror that structure rather than invent a new one. Nothing else in this codebase handles pagination, so this is the one prior art worth copying.

#### The one new problem pagination introduces

Today's `process_bid` opens a bid and returns to the results list with `driver.back()` (`scraper.py:544`). It also finds each bid's link by visible text — `(By.LINK_TEXT, number)` at `scraper.py:517` — because the Number cell has **no href**, only a JS click handler (`scraper.py:508-510`).

Material's paginator keeps its page index **in component memory, not in the URL**. So after opening a bid from page 4 and calling `back()`, the grid may well re-render at **page 1** — and every remaining bid on page 4 becomes unreachable by link text. Silently: the run would just skip them.

Three ways to handle it, in the order I'd try them:

1. **Capture the detail URL and navigate directly.** `process_bid` already waits for `"/detail/" in current_url` (`scraper.py:520`), so a real addressable URL exists *once you are on the page*. If that URL is stable and reachable by direct `GET`, the whole problem dissolves: collect all rows across all pages first, then visit each bid by URL with no pagination dance at all. **This is the clean solution** — but it needs the detail route to be directly addressable, which Phase 0 verifies.
2. **Page-at-a-time with position restore.** Process every bid on page *N*, then after each `back()` read the range label; if the grid reset, click Next until back at page *N*. Correct, but O(pages²) clicks on a large sweep.
3. **Open each bid in a new browser tab.** Leaves the results grid untouched in the original tab, so no position is ever lost. Costs a tab lifecycle per bid and depends on the JS handler tolerating a middle-click / `window.open`.

**Recommendation: probe for 1, fall back to 2.** I'll design the flow so the bid-visiting step is swappable between the two without restructuring anything else.

#### Does Export cover all pages?

`export_excel` (`scraper.py:576-593`) is the source of every metadata column in the final workbook — agency, ad type, dates, close date — because the results table itself carries only number and title. **Unknown: does the portal's Export button export the entire result set, or only the visible page?**

- If it exports everything → one export per run, as today.
- If it exports per page → one export per page, merged. `workbook.merge_exports` already merges multiple exports de-duplicated by ad number (it does this for keyword passes today), so the machinery exists either way.

Phase 0 answers this with one click.

### 2.2 The description — clean selector, confirmed

From the detail-page HTML you sent:

```html
<section _ngcontent-foe-c285="" id="mainSection" padding="">
  <p><p>Single Source Award to: <strong>APPLE, INC.</strong></p> ... </p>
</section>
```

`#mainSection` is a stable element ID. **No new selector guesswork, and no dependency on the export carrying a description column.** Revision 1's fallback plan is dropped.

Implementation notes from that sample:

- **Selector:** `(By.ID, "mainSection")`, read via `element.text`. The markup nests `<p>` inside `<p>`, which is invalid HTML — the browser re-parses and flattens it, so the live DOM tree does not match the source string. Reading rendered text sidesteps that entirely, and turns `&nbsp;` into ordinary spaces for free.
- **Ignore `_ngcontent-foe-c285`.** Per-build Angular scoping attribute; it will change without warning.
- **The description is richer than expected** — your sample carries the ad number, version, begin/end date-times, *and* the UNSPSC commodity codes with their titles (`43211500 Computers`, `43233004 Operating System Software`). Those commodity codes are a strong, structured classification signal, better than prose keyword matching. §6.3 uses them.
- **Extraction is best-effort.** A missing `#mainSection` records a warning and the bid is evaluated on title plus documents, with the workbook's `Description` column blank so the gap is visible rather than silent.

#### One observation, not a re-litigation

You've decided against kill-words and I'm building it that way. Worth noting factually: the bid you sampled is a Single Source *Intent to Award* notice whose own text says `"THIS IS NOT A COMPETITIVE SOLICITATION OR REQUEST FOR BIDS."` A status sweep with no type filter will pull in a fair number of these — award notices, informational notices, public meeting notices — and they will be classified onto your niche sheets alongside genuine opportunities.

That may be exactly what you want (visibility into who won what is useful intelligence). If it turns out to be noise later, the workbook's `Ad Type` column comes free from the portal export, so filtering or splitting on it is a small change at that point. **No action now.**

---

## 3. Where the new module lives

```
server/app/scrapers/myflorida/
├── scraper.py          ← UNTOUCHED (niche flow)
├── router.py           ← UNTOUCHED
├── ingest.py           ← UNTOUCHED
├── workbook.py         ← UNTOUCHED
├── commodity_codes.py  ← UNTOUCHED
└── sweep/              ← NEW — every file below is new
    ├── __init__.py
    ├── scraper.py      SweepScraper(MFMPScraper) — flow overrides + pagination
    ├── niches.py       niche catalogue + criteria (YOUR input)
    ├── evaluator.py    classification engine
    ├── documents.py    download → extract text → delete
    ├── workbook.py     multi-sheet writer
    ├── models.py       mfmp_sweep_bids table
    ├── export.py       DB persistence + workbook rebuild
    └── router.py       /myflorida/sweep/* endpoints
```

The run's `scraper` key is **`myflorida_sweep`**, distinct from `myflorida`, keeping run history, downloads and the exports page cleanly separated — and letting the sweep opt into bare-Excel delivery (§8) without touching the niche flow's ZIP.

---

## 4. Flow

| # | Step | Source |
|---|---|---|
| 1 | Launch Chrome, per-run download staging | `BaseScraper.start_driver` — **reused as-is** |
| 2 | Log in (3 retries for the stalling login page) | `MFMPScraper.login` — **reused as-is** |
| 3 | Open Advertisements, wait out the async cards | `MFMPScraper.open_advertisements` — **reused as-is** |
| 4 | Open Advanced Search, Max Results = 100 | **overridden** — parent's version minus the commodity accordion |
| 5 | Select Ad Status only | `MFMPScraper.select_ad_status` — **reused as-is** |
| 6 | Submit; detect results vs. empty | `MFMPScraper.submit_search` — **reused as-is** |
| 7 | Read rows on the current page | `MFMPScraper.collect_bids` — **reused as-is** |
| 8 | **Advance to the next page, repeat 7** | **new** — §2.1 |
| 9 | Export metadata workbook (per run, or per page) | `MFMPScraper.export_excel` — **reused as-is** |
| 10 | Per bid: open detail, read `#mainSection`, download documents | **overridden** |
| 11 | Extract document text, then delete the files | **new** |
| 12 | Evaluate → one niche | **new** |
| 13 | Write the multi-sheet workbook | **new** |
| 14 | Persist, archive, email | existing `archive_run` + `notify_scrape_completion` |

**Step 4.** The parent expands the Commodity Codes accordion whenever the run isn't in keyword mode (`scraper.py:248-258`). The sweep wants neither, so it overrides the method — no parent edit.

**Step 10.** Keeps the parent's navigation and download loop, adds the `#mainSection` read, and points downloads at a **temporary** per-bid folder under `_evaluation/` rather than a permanent `<ad number>_<title>` folder, since the files are deleted after extraction.

**Max Results stays at 100.** With working pagination, 100/page simply means fewer page loads for the same coverage.

---

## 5. Text extraction

`server/app/scrapers/sam/engine/text_extractor.py` already does this job: `build_full_text(description, docs_folder)` walks a folder, extracts `.pdf` via PyMuPDF and `.docx` via python-docx (with old-binary-`.doc` detection), reads `.txt` directly, skips the rest, and returns one string with each file under a `=== filename ===` heading. It contains nothing SAM-specific.

Two ways to consume it:

- **Import it directly** from `sam/engine/` — zero changes, but MyFlorida now depends on SAM's package layout.
- **Promote it to `app/core/text_extractor.py`** — a pure file move, SAM keeps working via a re-export.

**I recommend the promotion.** A second consumer is the moment shared infrastructure should stop living inside one portal's engine. It touches SAM's import line only, no logic. **Still awaiting your preference** — this is one of two open items in §12.

After extraction the per-bid folder is deleted, matching SAM's pattern (`sam_scraper.py:643`).

---

## 6. The evaluation wall

### 6.1 A lesson from SAM worth carrying over

SAM's evaluator classifies **title-primary**, and its source says why (`sam/engine/evaluator.py:832-838`):

> The full document body is a 120K-char dump of FAR boilerplate (which mentions inspection, training, audit, food, R&D, etc. in standard clauses) and must NOT drive Rule B/C matching — doing so falsely re-classifies hardware bids.

The same trap is here. Florida attachments carry standard terms mentioning printing, advertising, design and software in boilerplate. Weighting document text equally with the title would route a meaningful share of bids to the wrong sheet — and the failure is quiet, producing a plausible wrong answer rather than an error.

### 6.2 Weighting

| Source | Weight | Reason |
|---|---|---|
| **Title** | highest | states the actual requirement |
| **Description** (`#mainSection`) | high | real scope, now reliably available |
| **Commodity codes** in the description | high, and exact | structured, unambiguous — §6.3 |
| **Document text** | lowest; only terms you mark "strong" | boilerplate-heavy; confirms, never decides alone |

The description is weighted higher here than revision 1 assumed, because `#mainSection` turns out to be genuine scope prose rather than a stray summary field.

### 6.3 Commodity codes as a first-class signal

Your sample description embeds UNSPSC codes with titles:

```
43211500   Computers
43233004   Operating System Software
```

A regex over `#mainSection` for 8-digit codes gives an **exact** classification signal — no keyword ambiguity. If your criteria include commodity codes or prefixes per niche, a code hit can short-circuit scoring and assign the niche outright. If your criteria are keyword-only, this becomes a tie-breaker instead. Either way it's cheap and I'll build the extraction; how heavily it counts follows from your criteria.

### 6.4 One sheet per bid

Per your decision, no duplication. Scoring picks the **single highest-scoring niche**; ties break by your declared niche order. A bid whose best score falls below a **confidence threshold** goes to **Other**.

Runners-up are still recorded in an `Other Niches Considered` column — that keeps the routing auditable without duplicating rows, and it's the column that tells you whether the threshold is set right.

### 6.5 Criteria format — what I need from you

Strawman only; send your niches and criteria in whatever form is natural and I'll fit the structure to them.

```python
NICHES = {
    "<key>": {
        "label": "<full name>",
        "sheet": "<sheet tab name>",        # ≤31 chars — Excel's hard limit
        "order": 1,                          # sheet order, and tie-break priority
        "strong": [...],                     # decisive phrases
        "weak":   [...],                     # supporting terms
        "exclude": [...],                    # phrases that veto this niche
        "commodity_codes": [...],            # optional: exact codes or prefixes
    },
}
OTHER_SHEET = "Other"
CONFIDENCE_THRESHOLD = 2.0                   # tuned in Phase 5
```

The threshold is the most important knob and cannot be set honestly without one live run's output to look at.

---

## 7. The output workbook

One `.xlsx`, sheets in your declared order, `Other` last:

```
MyFlorida Sweep (open) [run_id].xlsx
├── <Niche 1>
├── <Niche 2>
├── ...
└── Other
```

Columns per sheet — the portal's export columns, plus:

| Column | Meaning |
|---|---|
| Niche | this sheet's niche (redundant, but survives copy-paste) |
| Match Score | the score that placed it here |
| Matched Criteria | which terms fired, and from where (title / description / documents) |
| Other Niches Considered | runners-up with scores |
| Description | the `#mainSection` text (truncated for cell limits) |
| Documents | filenames processed — the files themselves are gone |
| Document Text | characters extracted; `0` flags a bid judged on title + description alone |

`Document Text` matters: a scanned-image PDF yields nothing, and without this column a bid evaluated on a title is indistinguishable from one evaluated on 40 pages of scope.

**Empty sheets** are still created with headers, so the workbook shape is stable run to run. Say if you'd rather omit them.

**Styling** follows SAM's convention (`sam/export.py:30-36`): navy header row, auto-fit widths, illegal control characters stripped.

---

## 8. Delivery

Documents are deleted after extraction, so a sweep run produces **exactly one file** — the same situation as SAM. `myflorida_sweep` joins `EXCEL_ONLY_PORTALS` in `app/core/exports.py`, and the run archives, downloads and emails as a bare `.xlsx` with no ZIP wrapper.

The niche flow keeps its ZIP; it still has real document folders to carry.

---

## 9. API and UI

**API** — new router, existing one untouched:

| Endpoint | Purpose |
|---|---|
| `GET /myflorida/sweep/niches` | niche catalogue for the UI |
| `POST /myflorida/sweep/scrape` | body `{ ad_statuses: ["open"] }`; `?live_preview=` supported |
| `GET /myflorida/sweep/scrape/status/{run_id}` | poll |
| `GET /myflorida/sweep/scrape/runs` | history |

**Progress reporting.** With pagination the run has a real denominator. If `.mat-paginator-range-label` is present, `RunStatus` can show `"page 3 of 46 · 218 bids collected"` instead of a bare step name. Falls back to `"page 3"` if the label isn't there.

**UI placement** — I propose a **third mode inside the existing MyFlorida panel**, beside the current Codes / Keywords toggle: a "Full sweep" mode that hides the niche picker and shows the four Ad Status checkboxes. One console page, one mental model.

The alternative is a separate sidebar tile (`portals.ts`), which is more discoverable but implies MyFlorida is two different sources. **Recommendation: third mode. Still awaiting your preference** — second of the two open items in §12.

---

## 10. Isolation guarantee

You asked twice not to disturb the existing logic. Concretely:

- **`sweep/scraper.py` subclasses `MFMPScraper` and overrides three methods.** Subclassing adds behaviour without editing the parent, so the existing flow's code path is byte-identical.
- **No shared mutable state.** Different run key, different DB table, different router, different workbook writer.
- **The pagination code is new and lives only in the sweep.** The niche flow keeps its single-page assumption untouched — it never reaches 100 results anyway.
- **One trade-off I'll keep flagging rather than bury:** subclassing means a future fix to `MFMPScraper.login` or `submit_search` affects both flows. Usually a feature — portal fixes land in both — but it is real coupling. Full isolation would mean copying ~300 lines of login and search handling, which then drifts. **I recommend subclassing.**

Pre-existing files that change at all:

| File | Change |
|---|---|
| `server/main.py` | one `include_router` line |
| `server/app/core/exports.py` | add `"myflorida_sweep"` to `EXCEL_ONLY_PORTALS` |
| `server/app/scrapers/sam/engine/text_extractor.py` | **only if** you pick the §5 promotion — a file move plus a re-export |
| `client/src/lib/runs.ts` | mirror the excel-only set |
| `client/src/components/MyFloridaPanel.tsx` | the third-mode toggle |
| `client/src/lib/api.ts` | new client functions |

Additive only. No existing behaviour altered.

---

## 11. Build order

| Phase | Work | Output |
|---|---|---|
| **0** | **Probe run** — sweep by status, page through, export, open one bid. Answers: (a) is there a `.mat-paginator-range-label` with a total? (b) is the `/detail/` URL directly addressable (§2.1 option 1)? (c) does Export cover all pages or one? (d) how many records does an Open sweep actually return? | Findings note. **No production code.** |
| 1 | `niches.py` from your criteria + `evaluator.py` + unit tests over hand-written samples | Classification testable with no browser |
| 2 | `sweep/scraper.py` pagination loop + `#mainSection` read + `documents.py` | Text per bid, full coverage |
| 3 | `sweep/workbook.py` + `export.py` + `models.py` | The multi-sheet workbook |
| 4 | Router, delivery wiring, UI mode | End-to-end |
| 5 | Live run, threshold tuning against real output | Tuned |

Phase 0 is cheap and answers four questions that would otherwise be discovered expensively in Phase 2.

---

## 12. Risks

| Risk | Severity | Handling |
|---|---|---|
| `back()` resets the paginator, silently skipping bids | **High** | §2.1 — probe for direct detail URLs; position-restore fallback. The one thing Phase 0 must not miss. |
| Runtime — an unfiltered sweep parses documents for every ad; thousands of bids × several attachments is many hours | **High** | Now the dominant cost, since pagination lifted the 100 ceiling. Existing stop button works; I'd suggest an optional per-run bid cap for trial runs. **Tell me if you want one.** |
| Document text swamps title signal, mis-routing bids | Medium | Title-primary weighting (§6.1); `Document Text` column exposes it |
| Export covers only the visible page | Low | `merge_exports` already handles multi-export merging |
| Scanned PDFs yield no text | Low | Visible via `Document Text`; OCR out of scope |
| Threshold set wrong → everything lands in Other | Low | Phase 5 tunes against real output |
| Portal deploy changes Angular attributes | Low | Selectors anchored on IDs, `aria-label` and Material classes — never `_ngcontent-*` |

---

## 13. Still needed from you

**Blocking:**

1. **Your niche categories** — names, sheet tab names, order.
2. **Your evaluation criteria** per niche — any format; I'll adapt the structure. Include commodity codes or prefixes if you have them (§6.3), they're the strongest signal available.

**Non-blocking preferences** (defaults noted; I'll proceed on them if you'd rather not decide):

3. §5 — import SAM's text extractor, or promote it to `app/core/`? *(recommend: promote)*
4. §9 — third mode in the MyFlorida panel, or a separate sidebar tile? *(recommend: third mode)*
5. §12 — optional per-run bid cap for trial runs? *(recommend: yes, defaulting to unlimited)*
6. §7 — keep empty niche sheets, or omit them? *(recommend: keep)*

Nothing gets written until you say go.

---

## 14. Unrelated pre-flight note

`server/migrations/2026-07-28_add_sam_ollama_columns.sql` has still never been applied to your database — `sam_bids` is missing all three Ollama columns, so every SAM run fails its DB save and silently falls back to an in-memory sheet. That is why `sam_bids` has 0 rows.

It does **not** affect MyFlorida: `mfmp_bids` does have its `matched_keyword` column, so the July 16 migration was applied. Noted only because the new `mfmp_sweep_bids` table will want the same "did the migration actually run" check.
