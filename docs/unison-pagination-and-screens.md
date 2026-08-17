# Unison — the whole listing, early-exit screens, fewer manual reviews

| Layer | File |
| --- | --- |
| Listing walk / pagination | `server/app/scrapers/unison/engine/unison_scraper.py` |
| Run orchestration and reporting | `server/app/scrapers/unison/runner.py` |
| Screens, funnel mapping, strict fallback | `server/app/scrapers/unison/evaluation.py` |
| Shared decision funnel (**unchanged**) | `server/app/scrapers/sam/engine/evaluator.py` |

## 1. Why a 115-buy listing came back with 100

The walk was there and looked right. It failed on one line:

```python
NEXT_LINK = "//a[@title='Next Page']"
```

This portal renders pagination as a row *inside* the results table —
`< Prev  1 2  Next >` — the same row `extract_request_data` has always had to
skip. On that markup the anchor carries no `title`, so `next_page_url()` found
nothing, `collect_listing` read that as "last page", and page 2 was never
visited.

It was silent for a second reason. The guard that would have caught it compared
the rows read against the listing's own "1 - 100 of 115 Buys" line, which it
looked for in `span.page-summary` — an element this markup does not have either.
So the total came back `None`, the comparison was skipped, and a run fifteen buys
short finished clean.

**The fix, in four parts:**

* **Find the control in any of its shapes.** `NEXT_LINK_XPATHS` tries the titled
  anchor, then the link by its visible text, then any `page-links` anchor
  carrying a `pageNum`. Each candidate is checked for being *live* first — the
  last page renders the same "Next >" with the anchor disabled, which is a
  different thing from no control at all.
* **Find the summary anywhere.** `page_summary()` falls back to matching
  `N - M of T Buys` against the page text, and `page_counts()` returns all three
  numbers, so `last < total` is itself evidence that more pages exist.
* **Go by page number when the link cannot be found.** If the summary says buys
  remain and no Next anchor is recognised, the walk builds the next page's URL
  from the current one (the portal keeps `pageNum`/`pageSize`/`filterId` in the
  query string) rather than reporting a short listing as complete.
* **Hold the walk to the portal's number.** The total is read on the **first**
  page — read at the end it is the last page's summary, which agrees with itself
  however few pages were walked, and so could never catch an early stop. A
  mismatch is an `INCOMPLETE LISTING` error in the log and an error on the run;
  the console shows `Bids found / detected` so 100 / 115 is visible rather than
  inferred.

Rows are also de-duplicated by buy number across pages: a listing that shifts
under the walk re-renders a buy on the next page, and counted twice it hides a
row that was genuinely missed. Two things that guard has to get right, both
learned from a live run of 136 buys:

* **The key is `Buyer#`/`Detail URL`** — the fields `extract_request_data`
  actually builds. Reading `buyer_number` (the *hub's* field name, applied later
  by the runner's `_listing_records`) made every row key the empty string, so
  page 1's blank key matched page 2's and all 36 new buys were discarded as
  "already seen" — the count guard eating the rows it was meant to protect.
* **An unkeyed row is kept, never matched.** It is a row that cannot be
  compared, not one equal to every other unkeyed row.

The Next lookup is also **directional**: a candidate is only followed when its
`pageNum` is greater than the current page's. The later selectors match on href
shape rather than on the word "Next", and without the check the same run walked
off the last page into a page 3 that does not exist, read nothing there, and
reported the listing short.

### Numeric bounds in this path, audited

| Location | Bound | Verdict |
| --- | --- | --- |
| `runner.LIVE_PREVIEW_CEILING` | 1000 | How many records are mirrored into the run state for the console's table. **Was 100**, which showed 100 of 136 rows and read exactly like the truncation bug beside it. Never touched processing. |
| `collect_listing(max_pages=100)` | 100 **pages** | Runaway backstop — 10,000 buys at this page size. Hit only by a paginator that never terminates, and logged as an error when it is. |
| `filters.PAGE_SIZE` | "100" | The portal's own largest Show: option. A page size, not a total: the walk is what covers the rest. |
| `router.list_bids(limit=Query(100))` | 100 | Ordinary paged API over stored bids, with `offset`. Unrelated to scraping. |
| `extract_request_data` `description[:500]` | 500 chars | Truncates one field's text. No effect on the row set. |

The accumulator (`rows`) is declared before the page loop and only ever
`extend`ed, so page 2's records are added to page 1's rather than replacing
them; `seen_urls`/`seen_buys` live in the same scope for the same reason.

### The counter stream

```
[SEARCH EXECUTED]: Total 136 Bids Detected across Pages.
[PAGE 1]: Extracting rows 1 to 100...
 └── [PAGE 1 SUCCESS]: 100/100 processed. Running total 100 of 136.
[PAGINATING]: Navigating to Page 2...
[PAGE 2]: Extracting rows 101 to 136...
 └── [PAGE 2 SUCCESS]: 36/36 processed. Running total 136 of 136.
[LISTING COMPLETE]: Total Extracted: 136 / Total Detected: 136 (100% Coverage) across 2 page(s)
…
[PIPELINE COMPLETE]: Total Processed: 136 / Total Detected: 136 (100% Coverage)
```

Each page's row span comes from the portal's own summary line, so `SUCCESS` is
claimed against what that page *said* it held: a page yielding 90 of an
advertised 100 reads `[PAGE 1 SHORT]: 90/100 processed.` at the page that lost
them, rather than being absorbed into a total that only looks wrong at the end.
`[LISTING COMPLETE]` closes the walk; `[PIPELINE COMPLETE]` closes the run after
the detail pass, and the two agreeing is the guarantee the pipeline exists to
make.

## 1b. The Filter By criterion has to actually take

`Show: 100` reloads the listing, which detaches the Filter By `<select>` found
immediately after — and the criterion was dropped on a warning, so a run asking
for "Posted Today" quietly read the entire listing under a heading that said
otherwise. It is now retried on a stale element, and a failure is an error on
the run rather than a log line, because every count downstream is against the
wrong denominator.

The subtler half of the same bug: on a live run the criterion *was* applied and
the failure came from reading the option's text back for the log line. The label
read is now allowed to fail without invalidating the selection it describes.

## 2. Early-exit screens

Two decisions are made **before** the evaluation matrix, in
`evaluation.screen()`. They read fields the classifier is deliberately not given,
and they act on what the portal *declares* rather than on what prose implies:

| Screen | Field | Match | Result |
| --- | --- | --- | --- |
| `screen:gsa` | Buy Description, the General Information rows, bidding requirements, buy terms, Category/Subcategory | `GSA_PATTERN` — Schedule(s), contract(s), MAS or Federal Supply Schedule, any separator, case-insensitive | REJECT |
| `screen:cat` | Category, Subcategory | `hospitality`, `food service(s)`, `foods`, `catering`, `beverage`, `restaurant` | REJECT |

A screened buy never reaches the funnel — no documents are read through a
classifier that might disagree, and the rule code in the sheet says which screen
decided it.

### The GSA screen matches a pattern, not a list of strings

It used to be three literals — `gsa schedules`, `gsa schedule`, `gsa federal
supply schedule` — checked with `in`. That let a great deal through. A hyphen
(`GSA-Schedule`), an underscore, a doubled space, a line break, or the words
`contract` or `MAS` instead of `Schedule` were each enough to walk a GSA buy
past the screen and into the evaluation matrix. Six of seven live forms missed.

`GSA_PATTERN` replaces them:

```python
r"\bgsa[\s\-_]*(?:federal[\s\-_]*supply[\s\-_]*schedules?|schedules?|contracts?|mas)\b"
```

`[\s\-_]*` swallows whatever sits between the two words; the trailing `\b`
keeps it to whole words. `GSA_FORMS` lists every form the screen is expected to
catch and the tests assert the pattern against it, so the written statement and
the matcher cannot drift.

**Bare `GSA` is deliberately not a match.** The company is registered with GSA
and the word appears in registration boilerplate, GSA Advantage listings and
vendor blurbs on buys that are in scope. What puts a buy out is the *vehicle*,
not the mention; rejecting on the word would take good work off the table to
catch it. Tests pin `GSA Advantage`, `GSAB`, `Gsanchez` and a bare `MAS` as
passes.

### Where it runs: before the detail page is fetched

The GSA screen runs twice, and the first is the cheap one.

`screen_listing()` is **step 0 of the detail loop**, over the listing row alone.
The listing already carries the Buy Description, so a buy that names a GSA
vehicle there is out before its detail page is opened — a page load, its PDF
downloads and a text extraction saved on every hit, none of which could have
changed the answer. The buy stays in the report with its verdict and reason, and
reports zero documents rather than leaving the count for the sheet to guess at.

`screen()` then runs inside `evaluate()` over everything the detail page added,
for the buys that state their vehicle only there. Only GSA is screened at the
listing stage: the category screen reads Category and Subcategory, which the
listing does not carry.

Every interception says so out loud, naming the phrase and the field it was
found in — a rejection nobody can trace back to something on the page is one
nobody can check:

```
[FILTER TRIGGERED]: Bid #10492 | Match: 'gsa schedules' found in 'buy_description' | Action: Set Status -> REJECT (Bypassed Matrix)
```

The category screen is careful about one known trap:
`7B20 -- HARDWARE AND PERPETUAL LICENSE SOFTWARE` is the category the classifier
is kept away from precisely because "SOFTWARE" reads as a false reject. The
screens must not reintroduce it, and a test holds that line.

## 3. Fewer manual reviews

The shared funnel returns `MANUAL_REVIEW` for exactly one case: a **service**
matching neither the excluded list (Rule B) nor the allowed list (Rule C),
located in the US mainland. Every other path decides. That single case was
swallowing most of a Unison run, because Unison's buys are short reseller-style
descriptions that rarely name a service from either list.

**The funnel itself is unchanged** — it is shared with SAM, whose reviewers want
that queue, and editing it would silently change SAM's output. The resolution is
Unison-local, in `resolve_manual_review()`:

1. **Answer it from evidence the funnel never saw.** Unison publishes a Line Item
   table. A buy whose rows are quantified goods (≥50%, shipping ignored) is a
   supply whatever a terse description made the classifier think — PURSUE under
   Rule A.
2. **Fail closed on what remains.** A service on neither list with no product
   evidence is REJECT, naming why, rather than parking in a queue nobody empties.

Every verdict this touches keeps `decision_before_strict` on the record (and in
`raw_data`), and the run logs which buys it settled — the goal is fewer manual
reviews, not fewer traceable ones. `STRICT_FALLBACK = False` in
`evaluation.py` puts them all back in the queue.

## What a run reports now

```
[run abc] listing: read 115 of 115 buy(s) detected, across 2 page(s)
[run abc] decisions: {'PURSUE': 12, 'REJECT': 103} | 41 document(s) downloaded
          | 9 rejected by an early-exit screen | 31 resolved off the manual-review queue
[run abc] strict fallback settled: B-1029=REJECT, B-1044=PURSUE, …
```

`bids_detected`, `screened_out` and `manual_review_resolved` are on the run
record, so the console and the history can show them without re-reading the log.
