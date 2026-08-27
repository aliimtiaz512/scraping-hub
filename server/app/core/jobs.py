"""Where scrape runs actually execute: one pool, one cap, one queue.

Every portal's endpoint hands its run to `submit`, which puts it on a dedicated
thread pool and returns immediately. Two things follow from that pool being
*dedicated*:

  * **A run cannot starve the API.** FastAPI dispatches a sync endpoint — and a
    `BackgroundTasks` callable — into anyio's shared worker pool, which has 40
    tokens for the whole process. Every route in this app is a sync `def`, so a
    scrape parked in that pool for ten minutes holds a token the API needs.
    Running scrapes on their own executor keeps the two apart entirely.

  * **Concurrency is bounded.** Each run drives its own Chrome at roughly
    300–500 MB; enough of them at once and the OOM killer takes one mid-flight,
    which surfaces as a dead WebDriver session rather than as anything
    explicable (see BaseScraper.describe_failure). Runs past the cap wait in the
    queue as `queued` and start as slots free up, instead of all starting and
    some dying.

A queued run can be cancelled outright — its future is dropped before the work
begins, and it goes straight to `stopped`. A running one is interrupted the way
it always was, cooperatively, by run_manager/live.

The pool is also where a run's log tail comes from: the worker records which
run_id owns its thread, so `app.core.run_logs` can attribute every log line
emitted beneath it without a single scraper having to pass a run id around.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from app.config import settings
from app.core import run_manager, run_logs

logger = logging.getLogger(__name__)

# How many scrapes may run at once. The rest queue. Chrome's memory footprint is
# what sets this, not CPU — the work is almost entirely waiting on a browser.
CONCURRENCY = max(1, int(settings.scrape_concurrency))

_executor = ThreadPoolExecutor(max_workers=CONCURRENCY, thread_name_prefix="scrape")
_lock = threading.Lock()
# run_id -> the future doing (or waiting to do) that run's work.
_futures: dict[str, Future] = {}


def submit(run_id: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    """Queue `fn(*args)` as this run's work and return at once.

    The run is marked `queued` here rather than inside the worker, so a caller
    that polls immediately after starting a run sees the truth — queued behind
    others, or about to run — instead of a status that lags the queue.
    """
    def _work() -> None:
        # Claimed a slot: from here the run owns a thread until it finishes.
        run_logs.bind(run_id)
        run_manager.update_run(run_id, status="running", step="starting", queue_position=0)
        try:
            fn(*args, **kwargs)
        finally:
            run_logs.unbind()
            with _lock:
                _futures.pop(run_id, None)

    # Marked queued *before* the work is handed over: a free slot means the
    # worker sets "running" the instant it is submitted, and writing "queued"
    # afterwards would overwrite that and leave a running job looking queued.
    ahead = depth()
    starts_now = active() < CONCURRENCY and ahead == 0
    run_manager.update_run(
        run_id,
        status="queued",
        step="starting" if starts_now else ("queued (next up)" if ahead == 0 else f"queued (#{ahead + 1})"),
        queue_position=0 if starts_now else ahead + 1,
    )

    with _lock:
        future = _executor.submit(_work)
        _futures[run_id] = future

    logger.info(
        "[run %s] submitted (%d running, %d queued, cap %d)",
        run_id, active(), depth(), CONCURRENCY,
    )


def cancel(run_id: str) -> bool:
    """Drop a run that has not started yet. True if it was still in the queue.

    A run already executing cannot be cancelled this way — its thread is inside
    Selenium — so the caller falls back to the cooperative stop.
    """
    with _lock:
        future = _futures.get(run_id)
    if future is None or not future.cancel():
        return False
    with _lock:
        _futures.pop(run_id, None)
    logger.info("[run %s] cancelled before it started", run_id)
    return True


def active() -> int:
    """Runs executing right now."""
    with _lock:
        return sum(1 for f in _futures.values() if f.running())


def depth() -> int:
    """Runs waiting for a slot."""
    with _lock:
        return sum(1 for f in _futures.values() if not f.running() and not f.done())


def paused() -> int:
    """Runs holding a slot but doing nothing — parked at a checkpoint.

    Counted apart from `active()` because the two mean different things to
    someone deciding whether to start another run. A paused run has given back
    the network and the CPU, which is what pausing is for, but it is still
    standing on a thread with a browser open: the slot is not free, and a
    console that showed "1 running, cap 3" while two more were parked would be
    promising capacity that does not exist.
    """
    parked = run_manager.paused_runs()
    with _lock:
        return sum(1 for run_id in _futures if run_id in parked)


def stats() -> dict[str, int]:
    """What the console shows above the job list."""
    return {
        "running": active(), "queued": depth(),
        "paused": paused(), "capacity": CONCURRENCY,
    }
