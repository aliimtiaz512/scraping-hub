"""Run several BidNet niches in one execution, with nothing shared between them.

A single-niche run (`scraper.execute_run`) writes into the day's shared session
root and its ZIP is that whole root — by design, so a day's niches download as
one bundle. A **batch** is the opposite requirement: one execution, several
niches, and each niche's output containing that niche and nothing else. Both
exist; this module is the second.

    BidNet_Batch_2026-08-11_143002/     <- this execution's root, nobody else's
    ├── Graphic-Design/
    │   ├── Graphic-Design_Bids.xlsx    <- built from THIS run's rows only
    │   └── documents/…
    └── Commercial-Printing/
        ├── Commercial-Printing_Bids.xlsx
        └── documents/…

    archive_root/
    ├── BidNet_Batch_2026-08-11_143002_Graphic-Design.zip        <- per niche
    ├── BidNet_Batch_2026-08-11_143002_Commercial-Printing.zip
    └── BidNet_Batch_2026-08-11_143002.zip                       <- the execution

Four pieces, in the order the loop uses them:

    config_loader   what to search — a niche's keywords and NIGP codes, from the
                    catalog or from a caller-supplied config dict
    state_resetter  everything that must be false again before the next niche
    scraper_runner  one niche, start to finish, in its own browser session
    file_archiver   that niche's folder into that niche's ZIP; the execution's
                    folders into the execution's ZIP

Where the leakage came from
---------------------------
Three separate paths, all of which had to be closed — closing any two still
leaks:

1. **The workspace.** Niche folders are reused (`storage.niche_folder`), so an
   earlier run's spreadsheet and documents were still sitting there when the
   next one started, and got packaged as its output. A batch resets the folder
   instead (`storage.reset_niche_folder`).
2. **The spreadsheet.** `_refresh_niche_excel` regenerates a niche's sheet from
   *every run of that niche in the session*, so a re-run's sheet carried the
   earlier run's rows. A batch niche's sheet is regenerated from its own run id
   alone.
3. **The archive.** `_archive_bidnet` zips the whole session root, so a ZIP
   downloaded after running one niche contained every other niche run that day.
   A batch niche's ZIP holds one folder.

The browser is not shared either: each niche gets its own driver and its own
login, so no cookie, no results page and no sidebar state crosses from one niche
to the next. That costs a login per niche and is the point rather than an
oversight — the filters, the search box and the result group are all portal-side
state that a shared session would carry over.
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from app.config import settings
from app.core import run_logs, run_manager
from app.core.base_scraper import StopRequested
from app.core.filenames import sanitize_filename
from app.db import SessionLocal
from app.scrapers.bidnet import export, niches as catalog, storage
from app.scrapers.bidnet.filters import SidebarFilterRequest
from app.scrapers.bidnet.niches import KIND_NIGP, SearchTerm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# config_loader
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NicheJob:
    """One niche of a batch: what to search it with, and what to call its folder."""

    key: str
    label: str
    keywords: list[str] = field(default_factory=list)
    nigp_codes: list[str] = field(default_factory=list)
    slug: str | None = None

    def search_terms(self) -> list[SearchTerm]:
        """The queue the scraper works through — every keyword, then every code."""
        return (
            [SearchTerm(term) for term in self.keywords]
            + [SearchTerm(term, KIND_NIGP) for term in self.nigp_codes]
        )

    @property
    def total_terms(self) -> int:
        return len(self.keywords) + len(self.nigp_codes)


def config_loader(
    niche_keys: Iterable[str] | None = None,
    config: dict[str, dict[str, list[str]]] | None = None,
) -> list[NicheJob]:
    """What this batch will search, as one job per niche.

    Two sources, and the caller picks:

    * `config` — a plain dict, the shape a caller would keep in a JSON file::

          {"IT_Services": {"keywords": ["cloud migration"],
                           "nigp_codes": ["91828", "92000"]}}

      Used verbatim. Nothing is looked up, so a batch can search terms that are
      not in the catalog at all. A key that *does* name a catalog niche borrows
      its label and slug, so its folder is named the way that niche's folders
      are always named.

    * the catalog (`config` omitted) — the niches named by `niche_keys`, or
      every active niche when that is omitted too, read from the database with
      their keywords and NIGP codes.

    Niches with nothing to search are dropped here rather than in the loop: a
    job with no terms would open a browser, log in and search nothing.
    """
    if config is not None:
        return _jobs_from_config(config)

    session = SessionLocal()
    try:
        wanted = list(niche_keys) if niche_keys else [n.key for n in catalog.list_niches(session)]
        jobs: list[NicheJob] = []
        for key in wanted:
            niche = catalog.get_niche(session, key)
            if niche is None:
                logger.warning("[batch] unknown niche %r — skipped", key)
                continue
            terms = catalog.search_terms_for(session, key)
            job = NicheJob(
                key=key,
                label=niche.label,
                slug=niche.slug,
                keywords=[t.term for t in terms if t.kind != KIND_NIGP],
                nigp_codes=[t.term for t in terms if t.kind == KIND_NIGP],
            )
            if job.total_terms:
                jobs.append(job)
            else:
                logger.warning("[batch] niche %r has no search terms — skipped", key)
        return jobs
    finally:
        session.close()


def _jobs_from_config(config: dict[str, dict[str, list[str]]]) -> list[NicheJob]:
    session = SessionLocal()
    try:
        jobs = []
        for name, entry in config.items():
            entry = entry or {}
            niche = None
            try:
                niche = catalog.get_niche(session, name)
            except Exception:  # noqa: BLE001 — a config batch must not need the DB
                logger.info("[batch] catalog unavailable — using %r as its own label", name)
            job = NicheJob(
                key=name,
                label=(niche.label if niche else name),
                slug=(niche.slug if niche else None),
                keywords=_clean(entry.get("keywords")),
                nigp_codes=_clean(entry.get("nigp_codes")),
            )
            if job.total_terms:
                jobs.append(job)
            else:
                logger.warning("[batch] config entry %r has no terms — skipped", name)
        return jobs
    finally:
        session.close()


def _clean(values: Iterable[str] | None) -> list[str]:
    """Trimmed, de-duplicated, order kept, blanks dropped."""
    return list(dict.fromkeys((v or "").strip() for v in (values or []) if (v or "").strip()))


# ---------------------------------------------------------------------------
# state_resetter
# ---------------------------------------------------------------------------


class StateResetter:
    """Everything that must be untrue again before the next niche starts.

    The batch loop holds no scraped data of its own — each niche's records live
    on its own `BidnetScraper`, which is constructed inside the iteration and
    dropped at the end of it, so the in-memory reset is structural rather than a
    list of `.clear()` calls that a future field could be forgotten from. What
    this class exists for is the state that is *not* structural: the folder on
    disk, the reference the loop is still holding to the last scraper, and the
    log binding that would otherwise attribute this niche's lines to the last
    one.

    `reset()` runs before every niche, including the first — a batch root is
    new, but the first niche must not be the one iteration that skips the check.
    """

    def __init__(self, root: Path):
        self.root = root
        # The previous niche's scraper, kept only so it can be released. Never
        # read for data — carrying anything from it into the next niche is the
        # bug this whole module exists to prevent.
        self._previous: Any = None

    def reset(self, job: NicheJob) -> Path:
        """Clear the last niche's state and return a clean folder for this one."""
        released = self.release()
        folder = storage.reset_niche_folder(self.root, job.label, job.key, job.slug)
        logger.info(
            "[batch] [STATE RESET] %s: %s workspace %s emptied and recreated; "
            "no records, documents or browser session carried over",
            job.label,
            "previous scraper released; " if released else "",
            folder.name,
        )
        return folder

    def adopt(self, scraper: Any) -> None:
        """Hold this niche's scraper so the next `reset()` can let it go."""
        self._previous = scraper

    def release(self) -> bool:
        """Let go of the scraper being held. Called by `reset()` before each
        niche, and by the loop after the last one — which has no next iteration
        to release it."""
        scraper = self._previous
        if scraper is None:
            return False
        self._previous = None
        # The scraper closes its own browser and HTTP session in `run()`'s
        # finally block; this is the belt to that pair of braces, for a run that
        # died somewhere those did not reach.
        try:
            if getattr(scraper, "driver", None) is not None:
                scraper.cleanup()
        except Exception:  # noqa: BLE001 — a stuck browser must not end the batch
            logger.warning("[batch] releasing the previous niche's browser failed", exc_info=True)
        return True


# ---------------------------------------------------------------------------
# scraper_runner
# ---------------------------------------------------------------------------


def prepare_niche_run(
    job: NicheJob,
    folder: Path,
    batch_id: str,
    batch_root: Path,
    filters: SidebarFilterRequest,
    live_preview: bool = False,
) -> dict[str, Any]:
    """Register this niche's run before anything can go wrong with it.

    Separate from `scraper_runner` so the loop is holding a run id *before* the
    browser starts: a niche that dies mid-scrape still has a record to carry its
    errors, and still has its partial output packaged under its own name rather
    than vanishing with the exception.

    The run carries `batch_root`, which is what routes its packaging to the
    per-niche archiver instead of the day-session one.
    """
    return run_manager.create_run(
        "bidnet",
        folder,
        {
            "label": job.label,
            "niche": job.key,
            "niche_label": job.label,
            "niche_slug": job.slug,
            "niche_folder": str(folder),
            # Present instead of `session_root`: the two are mutually exclusive
            # and each selects its own packaging path (see exports.archive_run).
            "batch_root": str(batch_root),
            "batch_id": batch_id,
            "search": job.label,
            "keyword": (job.keywords or job.nigp_codes)[0],
            "keyword_count": len(job.keywords),
            "nigp_count": len(job.nigp_codes),
            "search_count": job.total_terms,
            "excel_exported": False,
            "live_preview": live_preview,
            "filters": filters.model_dump(exclude_none=True),
            "filters_summary": filters.summary(),
        },
    )


def scraper_runner(
    run: dict[str, Any], job: NicheJob, batch_id: str, filters: SidebarFilterRequest
) -> Any:
    """Scrape one niche, and return the scraper that did it.

    A fresh `BidnetScraper` on a fresh run id, so every counter, every seen-bid
    set and every accumulated record belongs to this niche alone — the in-memory
    isolation is structural, not something the loop has to remember to clear.

    The scraper comes back only so the caller can hand it to the state resetter:
    it is the thing that must be released before the next niche, and nothing
    else is ever read off it.
    """
    from app.scrapers.bidnet.scraper import BidnetScraper

    scraper = BidnetScraper(run["run_id"], job.search_terms(), filters, niche_label=job.label)
    # This niche's lines belong to this niche's run, not to the batch that
    # started it, so the console can stream one niche at a time.
    run_logs.bind(run["run_id"])
    try:
        scraper.run()
    finally:
        run_logs.bind(batch_id)
    return scraper


def ensure_niche_packaged(run_id: str) -> str | None:
    """Make sure this niche has its own ZIP, whatever happened to its run.

    A run that completed has already packaged itself — `BidnetScraper.run` calls
    `archive_run`, which routes back here through `archive_niche` because of the
    `batch_root` on the run. This is for the other case: a niche that raised
    before reaching that call. Its partial output is still that niche's own
    output, so it is bundled under its own name rather than left in the
    workspace to be deleted with it.

    Does nothing when the run already has a ZIP (so it never double-packages)
    or when the folder holds no files (so a niche that produced nothing does not
    ship an empty archive).
    """
    run = run_manager.get_run(run_id)
    if not run or run.get("zip_path"):
        return run.get("zip_path") if run else None
    folder = Path(run.get("niche_folder") or run.get("folder") or "")
    if not folder.is_dir() or not any(p.is_file() for p in folder.rglob("*")):
        return None
    logger.info(
        "[batch] [%s] packaging what the run produced before it stopped",
        run.get("niche_label"),
    )
    return archive_niche(run_id, run)


# ---------------------------------------------------------------------------
# file_archiver
# ---------------------------------------------------------------------------


def refresh_niche_excel(run_id: str, run: dict[str, Any]) -> None:
    """Rebuild this niche's sheet from the database — **this run's rows only**.

    The day-session equivalent (`exports._refresh_niche_excel`) deliberately
    merges every run of the same niche, because a re-run should add to that
    niche's bundle rather than replace it. In a batch that is precisely the
    leak: the execution's spreadsheet would carry rows scraped by runs the
    execution never made.

    Best-effort, and never destructive: the scraper has already written a sheet
    from its own in-memory records at this exact path, so a database that is
    unreachable — or that returns nothing — leaves that file standing.
    """
    folder = Path(run.get("niche_folder") or run.get("folder") or "")
    if not folder.is_dir():
        return
    out = storage.excel_path(
        folder, run.get("niche_label") or "", run.get("niche") or "", run.get("niche_slug")
    )
    staging = out.with_name(out.name + f".{run_id}.tmp")
    try:
        written = export.generate_excel(run_id, staging)
        if written == 0 and out.is_file():
            logger.warning(
                "[batch] [%s] the DB returned no rows — keeping the sheet the "
                "scraper wrote (%s)", run.get("niche_label"), out.name,
            )
            staging.unlink(missing_ok=True)
            return
        staging.replace(out)
        run_manager.update_run(run_id, excel_path=str(out), excel_name=out.name)
        logger.info(
            "[batch] [%s] spreadsheet %s holds %d row(s) from this run",
            run.get("niche_label"), out.name, written,
        )
    except Exception:  # noqa: BLE001 — the scraper's own sheet is already there
        logger.exception("[batch] could not refresh %s's spreadsheet", run.get("niche_label"))
        staging.unlink(missing_ok=True)


def archive_niche(run_id: str, run: dict[str, Any]) -> str | None:
    """One niche folder into one ZIP. Nothing else is in it.

    Called from `exports.archive_run` for any BidNet run carrying a `batch_root`,
    which is what keeps the batch's packaging out of the day-session path
    without either of them growing a flag to test.

    The workspace folder is **kept**: the batch's own ZIP is built from these
    folders when every niche has finished, and the whole root is removed then.
    """
    folder = Path(run.get("niche_folder") or run.get("folder") or "")
    if not folder.is_dir():
        logger.error("[batch] [%s] output folder is gone — nothing to package",
                     run.get("niche_label"))
        return None

    refresh_niche_excel(run_id, run)

    root_name = Path(run.get("batch_root") or "").name or "BidNet_Batch"
    out = settings.archive_root / (
        sanitize_filename(f"{root_name}_{folder.name}", max_length=150) + ".zip"
    )
    tmp = out.with_name(out.name + f".{run_id}.tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            _add_tree(zf, folder, arc_prefix=folder.name)
        tmp.replace(out)
    except Exception:  # noqa: BLE001 — packaging must never fail the run
        logger.exception("[batch] [%s] could not build the niche ZIP", run.get("niche_label"))
        tmp.unlink(missing_ok=True)
        return None

    run_manager.update_run(run_id, zip_path=str(out), zip_name=out.name)
    logger.info(
        "[batch] [ZIP] %s → %s (%s)",
        run.get("niche_label"), out.name, _size(out),
    )
    return str(out)


def archive_batch(batch_id: str, root: Path, keep_workspace: bool = False) -> str | None:
    """Every niche folder of the execution into one parent ZIP, then clean up.

    The per-niche ZIPs are the deliverable; this one exists for the case the
    requirement names second — an execution downloaded whole, with the niches as
    subfolders inside it. Both are built from the same tree, so they cannot
    disagree about what a niche contains.
    """
    if not root.is_dir():
        logger.error("[batch %s] root %s is gone — nothing to package", batch_id, root)
        return None
    folders = storage.niche_dirs(root)
    out = settings.archive_root / (sanitize_filename(root.name, max_length=150) + ".zip")
    tmp = out.with_name(out.name + f".{batch_id}.tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            _add_tree(zf, root, arc_prefix=root.name)
        tmp.replace(out)
    except Exception:  # noqa: BLE001
        logger.exception("[batch %s] could not build the execution ZIP", batch_id)
        tmp.unlink(missing_ok=True)
        return None
    logger.info(
        "[batch %s] [ZIP] execution bundle %s holds %d niche folder(s) (%s)",
        batch_id, out.name, len(folders), _size(out),
    )
    if not keep_workspace:
        shutil.rmtree(root, ignore_errors=True)
        logger.info("[batch %s] [CLEANUP] workspace %s removed", batch_id, root.name)
    return str(out)


def _add_tree(zf: zipfile.ZipFile, root: Path, arc_prefix: str = "") -> None:
    """Every file under `root`, stored under `arc_prefix/…`."""
    for path in sorted(root.rglob("*")):
        if path.is_file():
            arcname = Path(arc_prefix) / path.relative_to(root) if arc_prefix else path.relative_to(root)
            zf.write(path, arcname.as_posix())


def _size(path: Path) -> str:
    try:
        mb = path.stat().st_size / (1024 * 1024)
    except OSError:
        return "size unknown"
    return f"{mb:.1f} MB"


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------


def execute_batch(
    batch_id: str,
    jobs: list[NicheJob],
    filters: SidebarFilterRequest | None = None,
    live_preview: bool = False,
    keep_workspace: bool = False,
    root_name: str | None = None,
) -> dict[str, Any]:
    """Run every niche in turn, isolated from each other, and package each one.

    One niche failing is one niche missing from the bundle, never a batch that
    stops halfway: each iteration is wrapped, the failure is recorded against
    that niche's own run, the state is reset, and the next niche starts. A user
    pressing Stop is the one exception — that ends the batch, because it is an
    instruction about the batch rather than a fault in a niche.
    """
    filters = filters or SidebarFilterRequest()
    # The workspace is named for when the execution ran, not for the run id that
    # owns it: it is a folder a human opens, and it becomes the ZIP's name.
    root = storage.batch_root(root_name or storage.batch_name())
    resetter = StateResetter(root)
    outcomes: list[dict[str, Any]] = []

    logger.info(
        "[batch %s] starting: %d niche(s) — %s. Filters: %s",
        batch_id, len(jobs), ", ".join(job.label for job in jobs), filters.summary(),
    )
    run_manager.update_run(
        batch_id,
        status="running",
        step="running",
        # Named `batch_workspace`, not `batch_root`: the latter is what marks a
        # *niche* run for per-niche packaging (exports.archive_run), and the
        # parent is not a niche. Same folder, two different meanings.
        batch_workspace=str(root),
        niche_total=len(jobs),
        niche_done=0,
    )

    stopped = False
    for index, job in enumerate(jobs, start=1):
        progress = f"{index}/{len(jobs)}"
        logger.info(
            "[batch %s] [NICHE %s] START %s — %d keyword(s) + %d NIGP code(s)",
            batch_id, progress, job.label, len(job.keywords), len(job.nigp_codes),
        )
        run_manager.update_run(
            batch_id, step=f"niche {progress}: {job.label}", niche_current=job.label,
        )
        outcome: dict[str, Any] = {"niche": job.key, "label": job.label, "status": "failed"}
        run_id = ""
        try:
            folder = resetter.reset(job)
            run = prepare_niche_run(job, folder, batch_id, root, filters, live_preview)
            run_id = run["run_id"]
            outcome["run_id"] = run_id
            resetter.adopt(scraper_runner(run, job, batch_id, filters))
            finished = run_manager.get_run(run_id) or run
            outcome.update(
                status=finished.get("status") or "completed",
                bids=len(finished.get("bids") or []),
            )
        except StopRequested:
            # Not this niche's failure: the operator ended the execution.
            stopped = True
            outcome["status"] = "stopped"
            outcomes.append(outcome)
            logger.info("[batch %s] stopped by user after %s niche(s)", batch_id, index - 1)
            break
        except Exception as exc:  # noqa: BLE001 — one niche must not end the batch
            outcome["error"] = str(exc)[:300]
            outcome["status"] = "failed"
            logger.exception("[batch %s] [NICHE %s] FAILED %s", batch_id, progress, job.label)
            run_manager.add_error(
                batch_id, f"niche '{job.label}' failed: {exc.__class__.__name__} — {exc}"[:400],
            )
            if run_id:
                run_manager.add_error(run_id, str(exc)[:400])
                run_manager.update_run(run_id, status="failed", step="failed")

        if run_id:
            # Whatever happened above, this niche leaves with its own archive or
            # with none — never with a share of anyone else's.
            ensure_niche_packaged(run_id)
            outcome["zip_name"] = (run_manager.get_run(run_id) or {}).get("zip_name")
        logger.info(
            "[batch %s] [NICHE %s] %s %s — %s bid(s), packaged as %s",
            batch_id, progress, outcome["status"].upper(), job.label,
            outcome.get("bids", 0), outcome.get("zip_name") or "no archive",
        )
        outcomes.append(outcome)
        run_manager.update_run(batch_id, niche_done=index, niche_results=list(outcomes))

    # The last niche's browser has no next iteration to release it.
    resetter.release()

    zip_path = archive_batch(batch_id, root, keep_workspace=keep_workspace)
    succeeded = [o for o in outcomes if o["status"] == "completed"]
    failed = [o for o in outcomes if o["status"] == "failed"]
    logger.info(
        "[batch %s] finished: %d of %d niche(s) completed%s",
        batch_id, len(succeeded), len(jobs),
        f"; failed: {', '.join(o['label'] for o in failed)}" if failed else "",
    )
    # A batch is "completed" when anything came out of it, even if a niche
    # failed — the failures are on the run as errors and named per niche in
    # `niche_results`. It is only "failed" when every niche failed, because a
    # bundle with nothing in it is not a result.
    status = "stopped" if stopped else ("completed" if succeeded else "failed")
    run_manager.update_run(
        batch_id,
        status=status,
        step="done",
        niche_results=list(outcomes),
        niches_completed=len(succeeded),
        niches_failed=len(failed),
        zip_path=zip_path,
        zip_name=Path(zip_path).name if zip_path else None,
    )
    return {"batch_id": batch_id, "niches": outcomes, "zip_path": zip_path}
