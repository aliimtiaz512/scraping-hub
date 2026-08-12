# BidNet Direct — multi-niche batch runs

Several niches, one execution, sequential, with nothing shared between them.

`POST /bidnet/scrape` still runs a single niche into the day's shared session
root, and its ZIP is still that whole root — a day's niches downloaded as one
bundle. That is the right shape for the console's one-niche-at-a-time workflow
and is unchanged. This document is the other shape: **one execution, several
niches, each one's output containing that niche and nothing else.**

| Layer | File |
| --- | --- |
| Batch loop and its four parts | `server/app/scrapers/bidnet/batch.py` |
| Workspace / naming | `server/app/scrapers/bidnet/storage.py` (`batch_name`, `batch_root`, `reset_niche_folder`) |
| Packaging fork | `server/app/core/exports.py` (`archive_run`) |
| Endpoint | `POST /bidnet/scrape/batch` |
| UI | `client/src/components/BidnetPanel.tsx` — **Run all niches** |

## Starting one

From the console, **Run all niches** in the launch bar runs the whole catalog
with whatever sidebar filters are set. It is the one control that ignores the
niche dropdown, so it stays enabled with nothing selected — the only things that
block it are an empty catalog and an empty Purchasing Group (which BidNet
answers with no results whatever else is set). While it runs, the panel replaces
the results table with one row per niche: queued → running → its bid count and
its own Download link, since a batch's parent run holds no bids of its own.

Over the API:

```bash
# every active niche in the catalog, with its keywords and NIGP codes
curl -X POST localhost:8000/bidnet/scrape/batch -H 'content-type: application/json' -d '{}'

# a subset of the catalog
curl -X POST localhost:8000/bidnet/scrape/batch -H 'content-type: application/json' \
  -d '{"niches": ["graphic_design", "pcb_electronics"],
       "filters": {"status": "OPEN", "published_date": {"type": "WITHIN", "within": "WEEK"}}}'

# terms supplied inline — no catalog involved at all
curl -X POST localhost:8000/bidnet/scrape/batch -H 'content-type: application/json' -d '{
  "config": {
    "IT_Services":  {"keywords": ["cloud migration", "cybersecurity"], "nigp_codes": ["91828", "92000"]},
    "Construction": {"keywords": ["paving", "roofing"],                "nigp_codes": ["91319", "91400"]}
  }
}'
```

It returns immediately with a `batch_id` — poll it on
`/bidnet/scrape/status/{batch_id}` like any run. `niche_results` on that record
names each niche's own run id, status and ZIP as it finishes, so each niche is
downloadable from `/download/{run_id}` on its own.

**One queued job, not one per niche.** The niches run in order, one at a time;
submitting them separately would let the job queue run them in parallel, which
is exactly what a batch must not do — two Chrome sessions on one BidNet account,
and no way to say which niche a portal-side state belongs to.

## The four parts

```
config_loader    what to search       catalog keys, or an inline config dict
state_resetter   what to forget       the niche folder, the previous browser
scraper_runner   one niche            fresh run id, fresh BidnetScraper, fresh driver
file_archiver    what comes out       one ZIP per niche + one for the execution
```

```
for each niche:
    state_resetter.reset(job)      →  an EMPTY  <batch root>/<Niche>/
    prepare_niche_run(...)         →  its own run id, marked with batch_root
    scraper_runner(...)            →  BidnetScraper.run() in its own browser
    ensure_niche_packaged(...)     →  <Batch>_<Niche>.zip, even if the run died
archive_batch(...)                 →  <Batch>.zip, then the workspace is deleted
```

Console output per niche:

```
[batch a1b2c3] [NICHE 1/2] START Graphic Design — 22 keyword(s) + 5 NIGP code(s)
[batch] [STATE RESET] Graphic Design: workspace Graphic-Design emptied and recreated; …
… the niche's own run logs, bound to its own run id …
[batch] [ZIP] Graphic Design → BidNet_Batch_2026-08-11_143002_Graphic-Design.zip (4.2 MB)
[batch a1b2c3] [NICHE 1/2] COMPLETED Graphic Design — 37 bid(s), packaged as …
[batch a1b2c3] [NICHE 2/2] START Commercial Printing — …
[batch a1b2c3] [ZIP] execution bundle BidNet_Batch_2026-08-11_143002.zip holds 2 niche folder(s)
[batch a1b2c3] [CLEANUP] workspace BidNet_Batch_2026-08-11_143002 removed
[batch a1b2c3] finished: 2 of 2 niche(s) completed
```

## The leakage, and why it took three fixes

Records and documents from earlier niches were turning up in a niche's output.
There was no single cause — three independent paths carried it, and closing any
two of them still leaks:

1. **The workspace.** `storage.niche_folder` *reuses* an existing folder, on
   purpose: a re-run within a day's session belongs with that niche's earlier
   files. So the previous execution's spreadsheet and documents were still
   sitting in the folder when the next run was packaged, and shipped as its
   output. A batch calls `storage.reset_niche_folder` instead — remove, recreate
   — so each niche starts from a directory whose contents are known.
2. **The spreadsheet.** `exports._refresh_niche_excel` regenerates a niche's
   sheet from *every run of that niche in the session*, again on purpose, so a
   re-run adds to the bundle rather than replacing it. In a batch that is the
   leak in its most convincing form — a spreadsheet full of real rows this
   execution never scraped. `batch.refresh_niche_excel` regenerates from the one
   run id.
3. **The archive.** `exports._archive_bidnet` zips the whole session root, so a
   ZIP downloaded after one niche held every other niche run that day.
   `archive_run` now forks on the run's fields: `batch_root` → one niche folder
   (`batch.archive_niche`), `session_root` → the day's root, and the two are
   never both set.

The browser is not shared either. Each niche gets its own driver and its own
login, so no cookie, results page, result group or sidebar filter state crosses
between niches. That costs a login per niche and is the point — the sidebar
filters are applied once per run and persist across searches *by design* (see
`bidnet-sidebar-filters.md`), which is exactly the kind of state that must not
outlive its niche.

In-memory isolation is structural rather than a list of things to clear: every
counter, the `seen_bid_ids` set and the accumulated records live on the
`BidnetScraper` instance, which is constructed inside the iteration and dropped
at the end of it. The batch loop itself holds no scraped data. That is why
`StateResetter` only deals with what is *not* structural — the folder, the
previous browser, and the log binding.

## Failure handling

Each niche is wrapped. A niche that times out or throws is logged, recorded as
an error on both the batch and its own run, and packaged with whatever it did
produce (nothing, if it produced nothing — an empty archive is worse than no
archive). Then the loop resets state and moves to the next niche.

* A batch is `completed` if any niche completed, `failed` only if all of them
  failed — a bundle with nothing in it is not a result.
* Stop is the exception: `StopRequested` ends the execution, because it is an
  instruction about the batch rather than a fault in a niche.

## Retention

The batch workspace is deleted once its ZIPs are written (`keep_workspace: true`
in the request keeps it for debugging). The archives themselves live in
`archive_root` under the normal retention. This is the opposite of the
day-session path, where the workspace is deliberately kept so later niches can
join it — a batch has no later niches to wait for.
