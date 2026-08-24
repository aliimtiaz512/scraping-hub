"""The broad sweep: every member agency bid the sidebar filters allow, in one sheet.

This is the second of BidNet's two execution modes, and the *opposite* of the
first in the one way that matters — what goes into the search box.

    Run all niches (batch.py)         Run all member agency bids (here)
    ------------------------------    ---------------------------------------
    one run per niche                 one run, full stop
    every keyword + NIGP code of      **no search terms at all**
      that niche, one search each
    filtered by the sidebar           filtered by the sidebar
    one sheet per niche, in a ZIP     one consolidated sheet, no ZIP

A niche run answers "what is open in graphic design"; this answers "what is
open, full stop, under the dates I picked". The keyword box is left empty on
purpose: typing anything into it is what narrows the portal's list, so the way
to see all of it is to search nothing and let the sidebar do the narrowing.

The flow, and why it is these five steps in this order:

    login
    open_filtered_session       <- an empty-keyword search opens the portal's
                                   whole open list, and the sidebar (which only
                                   renders on a results page) is driven into the
                                   requested state on it
    filter_member_agency        <- click the "Member Agency Bids" result group;
                                   the other groups are state/federal and are
                                   not what this sweep is for
    collect_links               <- walk every results page
    for each solicitation: open it, read its fields, read its agency

Then one spreadsheet — `bidnet_member_agencie_<date>.xlsx`, the client's
spelling — delivered on its own. No ZIP: a ZIP exists to hold several files,
and a consolidated sweep produces exactly one.

Scale is the thing to keep in mind here. A niche keyword returns tens of bids;
an unfiltered member-agency list returns *thousands*, and each one is a detail
page load. That is why the sidebar's date panels matter far more in this mode
than in the other one — they are the only thing between the sweep and every
open solicitation on the portal — and why `MAX_SWEEP_BIDS` exists below.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from selenium.common.exceptions import TimeoutException, WebDriverException

from app.core import run_manager
from app.core.base_scraper import StopRequested
from app.core.exports import archive_run
from app.scrapers.bidnet import export, storage
from app.scrapers.bidnet.filters import SidebarFilterRequest
from app.scrapers.bidnet.scraper import (
    HEADLESS_MODE,
    STATUS_ACK_REQUIRED,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_PARTIAL,
    BidnetScraper,
    _shown,
)
from app.services.notifier import notify_scrape_completion

logger = logging.getLogger(__name__)

# What the `Niche` column says for a bid whose issuing agency the detail page
# does not label. The column is never left blank: a row with no heading at all
# reads as a bug in the export rather than as a portal that did not say.
UNKNOWN_AGENCY = "Member Agency (unnamed)"

# The heading the run itself is filed under, in the console and the DB.
SWEEP_LABEL = "All member agencies"

# How much of an agency's name is kept. The `niche` column it lands in is a
# VARCHAR, and Postgres does not truncate — it fails the INSERT, which rolls
# back the **whole run's** save, so one verbose agency costs every bid the sweep
# collected. Widening the column (255, see models.py) is the fix; this is the
# guarantee that the same class of failure cannot come back from a name longer
# than whatever the column happens to be. Kept below the column width, so a
# capped value still fits with room to spare.
MAX_AGENCY_LENGTH = 200

# A ceiling on how many solicitations one sweep will open. This is a guard, not
# a filter: an unfiltered member-agency list is a few thousand bids and one
# detail page load each, so a sweep launched with the date panels left open
# would run for most of a day. Hitting it is reported as a run warning naming
# the sidebar, because narrowing the dates — not raising this — is the fix.
#
# In practice `scraper.MAX_PAGES` bounds the harvest first (100 pages of 25 rows
# is 2,500 links), and it warns when it does. This is the belt to that brace:
# it bounds the *expensive* half — the detail page loads — independently of how
# many rows a page happens to hold.
MAX_SWEEP_BIDS = 5000

# How far the collected count may fall short of the portal's own before the run
# calls it an incomplete harvest: the larger of these two. Some slack is
# legitimate — the badge counts solicitations while the walk collapses rows the
# portal repeats as the list shifts under pagination — but only some. 574 of
# 1,850 is not slack.
HARVEST_SLACK = 5
HARVEST_SLACK_RATIO = 0.02


class MemberAgencySweepScraper(BidnetScraper):
    """A BidnetScraper that searches nothing and collects the whole group.

    Everything below the search box is inherited unchanged — the login, the
    sidebar driver, the pagination walk, the detail scrape, the acknowledgement
    handling, the deduplication. What is overridden is `run`, because the phase
    that iterates a niche's search terms has no counterpart here: there is one
    search, it is empty, and it is the one `open_filtered_session` already makes.
    """

    def __init__(self, run_id: str, filters: SidebarFilterRequest | None = None):
        super().__init__(run_id, [], filters, niche_label=SWEEP_LABEL)
        # The day the sweep belongs to, fixed at construction so a run that
        # crosses midnight writes one filename rather than two.
        run = run_manager.get_run(run_id) or {}
        self.sweep_date = _run_date(run)

    # -- the sheet ----------------------------------------------------------

    @property
    def excel_path(self) -> Path:
        return self.niche_folder / storage.member_agency_excel_name(self.sweep_date)

    def write_excel(self, records: list[dict]) -> None:
        """The one deliverable, straight from the records just scraped.

        Regenerated from the database afterwards by the packaging step, exactly
        like a niche sheet — this copy is what survives a database that is down,
        which for a sweep of several thousand bids is the difference between a
        long run delivering and a long run being lost.
        """
        self.set_step("generating_excel")
        out_path = self.excel_path
        try:
            written = export.generate_excel_from_records(records, out_path)
            run_manager.update_run(
                self.run_id, excel_path=str(out_path), excel_name=out_path.name
            )
            logger.info(
                "[run %s] wrote %s member agency bid(s) to %s",
                self.run_id, written, out_path.name,
            )
            if written != len(records):
                message = (
                    f"spreadsheet holds {written} rows but {len(records)} bids were "
                    f"collected"
                )
                logger.error("[run %s] %s", self.run_id, message)
                run_manager.add_error(self.run_id, message)
        except Exception:  # noqa: BLE001 — never fail a whole sweep over the sheet
            logger.exception("[run %s] member agency Excel generation failed", self.run_id)
            run_manager.add_error(self.run_id, "excel generation failed (see logs)")

    # -- the sweep ----------------------------------------------------------

    def open_member_agency_list(self) -> int | None:
        """Filters applied, keyword box empty, Member Agency Bids selected.

        Returns the portal's own count for that group, or None when it could not
        be read — which the caller treats as "unknown, carry on" rather than as
        zero, the same way a niche run does.
        """
        # An empty-keyword search opens the portal's whole open list; the
        # sidebar is driven on that page and every filter it applies stays in
        # force for what follows.
        self.open_filtered_session()
        self.ensure_logged_in()
        self._ensure_filters_live()

        self.filter_member_agency()
        # Before a row is read, and before the filters are verified: ask the
        # grid for 100 rows a page. This is the single highest-value line in the
        # sweep — at the portal's default of 25 an 1,850-bid list is a 74-page
        # walk, and every page transition is a postback that can be intercepted,
        # go stale, or simply not render. At 100 it is 19.
        size = self.set_page_size()
        run_manager.update_run(self.run_id, page_size=size)
        # The group click and the page-size change are both searches in their
        # own right, so the filters are verified against the page they left
        # behind rather than assumed to have survived them.
        self.confirm_filters_active(SWEEP_LABEL)
        if not self._ensure_first_result_page():
            # Harvesting from page 7 would silently lose pages 1-6. Rebuild the
            # session and re-select the group rather than collect a partial list
            # and report it as the whole sweep.
            logger.warning(
                "[run %s] could not return the member agency list to page 1 — "
                "rebuilding the filtered session", self.run_id,
            )
            self._filters_live = False
            self._ensure_filters_live()
            self.filter_member_agency()
        return self.result_count()

    def _reconcile_harvest(self, count: int | None, harvest) -> None:
        """What the portal said it had, against what the walk actually brought
        back.

        The check the sweep exists to pass. Its failure mode is not an exception
        but a *plausible smaller number* — 574 bids where the portal offered
        1,850 reads as a real result set to anyone who was not counting. So the
        two numbers are compared out loud, every run, and a shortfall is an
        error on the run rather than a line in a log nobody opens.

        A little slack, because the two are not measuring quite the same thing:
        the badge counts solicitations, and rows the portal repeats across a
        shifting list are collapsed here. Anything past that slack is a walk
        that lost pages.
        """
        if not count:
            return
        collected = len(harvest.links)
        shortfall = count - collected
        if shortfall <= max(HARVEST_SLACK, int(count * HARVEST_SLACK_RATIO)):
            logger.info(
                "[run %s] [RECONCILED]: portal offered %s member agency bid(s), "
                "the walk collected %s.", self.run_id, count, collected,
            )
            return
        message = (
            f"Incomplete harvest: BidNet reported {count} member agency bid(s) and "
            f"only {collected} were collected — {shortfall} missing. The walk did "
            f"not reach every results page; see the pagination errors above."
        )
        logger.error("[run %s] %s", self.run_id, message)
        run_manager.add_error(self.run_id, message)

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        self._save_run_row()
        try:
            live_preview = bool((run_manager.get_run(self.run_id) or {}).get("live_preview"))
            headed = live_preview or not HEADLESS_MODE
            logger.info("[JOB INITIALIZED]: Portal: BidNet Direct — ALL MEMBER AGENCY BIDS")
            logger.info(
                " ├── [EXECUTION MODE]: %s (Live Preview: %s)",
                "Visible browser" if headed else "Headless Background Run",
                "ON" if live_preview else "OFF",
            )
            logger.info(" ├── [SEARCH TERMS]: none — the keyword box is left empty on purpose")
            logger.info(" └── [FILTERS APPLIED]: %s", self.filters.summary())

            self.start_driver(headless=None if HEADLESS_MODE else False)
            self.login()

            # PHASE 1 — one search, no keyword, the whole Member Agency group.
            count = self.open_member_agency_list()
            logger.info(
                "[run %s] [PORTAL DETECTED]: %s member agency bid(s) under %s",
                self.run_id, _shown(count), self.filters.summary(),
            )
            if count == 0:
                logger.info(
                    "[run %s] the Member Agency group is empty under this run's "
                    "filters — nothing to sweep", self.run_id,
                )
                run_manager.update_run(self.run_id, no_results=True)

            self.set_step("collecting_links")
            harvest = self.collect_links()
            self._reconcile_harvest(count, harvest)
            self._rows_detected += harvest.rows_detected
            self._rows_parsed += harvest.rows_parsed
            self._rows_failed += harvest.rows_failed
            logger.info(
                "[run %s] [HARVEST]: %s row(s) rendered, %s parsed, %s dropped "
                "(%s unreadable, %s repeated across pages) → %s solicitation(s)",
                self.run_id, harvest.rows_detected, harvest.rows_parsed,
                harvest.rows_dropped, harvest.rows_failed, harvest.duplicates,
                len(harvest.links),
            )

            # Deduplicated by solicitation id, as a niche run does — the portal
            # repeats a row when the list shifts under pagination, and a sweep
            # of eighty pages sees far more of that than a keyword's two.
            links: list[str] = []
            seen: set[str] = set()
            for link in harvest.links:
                bid_id = self._bid_key(link)
                if bid_id in seen:
                    self._link_duplicates += 1
                    continue
                seen.add(bid_id)
                links.append(link)
            run_manager.update_run(self.run_id, bids_found=len(links))

            capped = len(links) > MAX_SWEEP_BIDS
            if capped:
                message = (
                    f"The member agency list holds {len(links)} solicitations, more "
                    f"than this sweep will open in one run ({MAX_SWEEP_BIDS}). The "
                    f"first {MAX_SWEEP_BIDS} were scraped. Narrow the sidebar's "
                    f"Published/Closing Date panels and re-run to cover the rest — "
                    f"the current filters are {self.filters.summary()}."
                )
                logger.warning("[run %s] %s", self.run_id, message)
                run_manager.add_warning(self.run_id, message)
                links = links[:MAX_SWEEP_BIDS]

            # PHASE 2 — open each solicitation once and read it. One page load
            # each: attachments are not downloaded in either mode.
            self.set_step("collecting_bids")
            all_records: list[dict] = []
            for index, link in enumerate(links, start=1):
                record: dict[str, Any] | None = {
                    "reference_number": None, "title": None, "error": None,
                }
                try:
                    record = self.process_bid(link)
                    if record is not None:
                        # The `Niche` column, for a run that searched no niche:
                        # the agency that issued the bid. Read after the fields
                        # so it comes off the solicitation page rather than off
                        # an acknowledgement interstitial.
                        agency = (
                            self._extract_agency(link)
                            if record.get("status") != STATUS_ACK_REQUIRED
                            else ""
                        )
                        record["niche"] = _agency_name(agency)
                except StopRequested:
                    raise
                except (TimeoutException, WebDriverException) as exc:
                    record["error"] = str(exc)[:300]
                    record["niche"] = UNKNOWN_AGENCY
                    run_manager.add_error(self.run_id, f"bid failed: {exc.__class__.__name__}")
                    self.screenshot(f"bid_{index}")
                if record is None:
                    continue
                # No keyword found this bid — the sweep searched none.
                record["matched_keyword"] = ""
                if not self._claim_bid(self._bid_key(link), record, []):
                    continue
                run_manager.add_bid_result(self.run_id, record)
                all_records.append(record)
                if index % 50 == 0:
                    logger.info(
                        "[run %s] [PROGRESS]: %s/%s solicitation(s) read",
                        self.run_id, index, len(links),
                    )

            self._report(count, links, all_records)
            self.write_excel(all_records)

            run = run_manager.get_run(self.run_id) or {"run_id": self.run_id}
            try:
                stored = export.save_bids(run, all_records)
                run_manager.update_run(self.run_id, bids_stored_in_db=stored)
                logger.info(
                    "[run %s] [DB SAVE]: %d record(s) in memory → %d row(s) stored.",
                    self.run_id, len(all_records), stored,
                )
            except Exception:  # noqa: BLE001 — DB issues shouldn't abort the run
                logger.exception("[run %s] DB save failed", self.run_id)
                run_manager.add_error(self.run_id, "db save failed (see logs)")
                # The sheet written above is now the only copy of the rows, so
                # the packaging step must not regenerate over it from an empty
                # database. See exports.excel_bytes.
                run_manager.update_run(self.run_id, db_save_failed=True)

            run_manager.update_run(self.run_id, excel_exported=True)
            self.set_step("packaging_results")
            archive_run(self.run_id)
            run_manager.update_run(self.run_id, status="completed", step="done")
            notify_scrape_completion(self.run_id, "bidnet", len(all_records))
        except StopRequested:
            logger.info("[run %s] stopped by user", self.run_id)
        except Exception as exc:  # noqa: BLE001 — a failed run is reported, not raised
            logger.exception("[run %s] member agency sweep failed", self.run_id)
            self.screenshot("fatal")
            run_manager.add_error(self.run_id, str(exc)[:500])
            run_manager.update_run(self.run_id, status="failed", step="failed")
        finally:
            self.cleanup()
            run_manager.update_run(self.run_id, finished_at=datetime.now().isoformat())
            self._save_run_row()

    def _report(self, count: int | None, links: list[str], records: list[dict]) -> None:
        """The sweep as one funnel, and what each agency contributed.

        The per-agency tally is the sweep's own version of the niche breakdown a
        batch prints: a consolidated sheet's whole point is that it spans
        agencies, and a run that says only "1,412 bids" cannot be checked against
        the portal by anyone.
        """
        by_status = Counter(r.get("status") or STATUS_OK for r in records)
        logger.info(
            "[FUNNEL] run %s | portal reported: %s | rows rendered: %d | rows parsed: %d "
            "| rows unparseable: %d | unique solicitations: %d | exported: %d",
            self.run_id, _shown(count), self._rows_detected, self._rows_parsed,
            self._rows_failed, len(links), len(records),
        )
        logger.info(
            "[SUMMARY] run %s | Fully extracted: %d | Partial: %d | Failed: %d "
            "| Acknowledgement required: %d | Skipped (duplicate): %d",
            self.run_id, by_status[STATUS_OK], by_status[STATUS_PARTIAL],
            by_status[STATUS_FAILED], by_status[STATUS_ACK_REQUIRED],
            self._duplicates_skipped,
        )
        run_manager.update_run(
            self.run_id,
            rows_detected=self._rows_detected,
            rows_parsed=self._rows_parsed,
            rows_unparseable=self._rows_failed,
            bids_fully_extracted=by_status[STATUS_OK],
            bids_partial=by_status[STATUS_PARTIAL],
            bids_extraction_failed=by_status[STATUS_FAILED],
            bids_acknowledgement_required=by_status[STATUS_ACK_REQUIRED],
            duplicates_skipped=self._duplicates_skipped,
            acknowledgements_required=self._acknowledgement_required,
            acknowledgements_accepted=self._accepted_acknowledgements,
        )

        agencies = Counter((r.get("niche") or UNKNOWN_AGENCY) for r in records)
        if agencies:
            logger.info(
                "[AGENCIES] run %s | %d agency/agencies: %s",
                self.run_id, len(agencies),
                "; ".join(f"{name} ({n})" for name, n in agencies.most_common()),
            )
            run_manager.update_run(
                self.run_id,
                agency_total=len(agencies),
                agency_breakdown=dict(agencies.most_common()),
            )

        if self._rows_detected and not records:
            message = (
                f"Nothing was exported although BidNet rendered {self._rows_detected} "
                f"member agency row(s) — see the per-row warnings above."
            )
            logger.error("[run %s] %s", self.run_id, message)
            run_manager.add_error(self.run_id, message)


def _agency_name(value: str) -> str:
    """An agency name the export and the database can both hold.

    Never blank (see UNKNOWN_AGENCY) and never longer than the column — a name
    that overflows fails the run's entire insert rather than its own row, which
    is how one agency with a long title lost a whole sweep's worth of bids.
    """
    name = (value or "").strip()
    if not name:
        return UNKNOWN_AGENCY
    return name if len(name) <= MAX_AGENCY_LENGTH else name[: MAX_AGENCY_LENGTH - 1] + "…"


def _run_date(run: dict[str, Any]) -> date:
    """The day the run started, for the filename. Today if it cannot be read."""
    started = run.get("started_at")
    if started:
        try:
            return datetime.fromisoformat(str(started)).date()
        except ValueError:
            pass
    return date.today()


def execute_member_agency_sweep(
    run_id: str, filters: SidebarFilterRequest | None = None
) -> None:
    """Entry point for the job queue — see `router.start_member_agency_sweep`."""
    MemberAgencySweepScraper(run_id, filters).run()
