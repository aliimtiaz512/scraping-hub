"""The background job pool: the cap, the queue, cancelling, and the log tails.

No browsers and no portals — the work these submit is a stand-in. What they pin
down is the machinery every scrape now goes through: that a fourth run waits
instead of starting, that a waiting run can be dropped before it does anything,
and that each run's log lines land in its own tail.

    server/.venv/bin/python -m pytest server/tests/test_jobs.py
"""

import logging
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import jobs, run_logs, run_manager  # noqa: E402


@pytest.fixture
def gate():
    """A latch the fake work waits on, so a test controls when jobs finish."""
    event = threading.Event()
    yield event
    event.set()          # never leave a pool thread blocked
    time.sleep(0.2)


def _run() -> str:
    return run_manager.create_run("demo", Path("/tmp"))["run_id"]


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


# -- the cap ------------------------------------------------------------------


def test_runs_past_the_cap_wait_instead_of_starting(gate):
    """The cap exists because each run drives a browser; over-subscribing the
    host loses one to the OOM killer rather than running them all."""
    started: list[str] = []

    def work(run_id):
        started.append(run_id)
        gate.wait(10)

    submitted = [_run() for _ in range(jobs.CONCURRENCY + 2)]
    for run_id in submitted:
        jobs.submit(run_id, work, run_id)

    assert _wait_for(lambda: len(started) == jobs.CONCURRENCY)
    time.sleep(0.3)                       # give a queued one a chance to misbehave
    assert len(started) == jobs.CONCURRENCY
    assert jobs.stats() == {
        "running": jobs.CONCURRENCY, "queued": 2, "capacity": jobs.CONCURRENCY,
    }

    gate.set()
    assert _wait_for(lambda: len(started) == len(submitted))


def test_a_running_job_reports_running_not_queued(gate):
    """The status is written before the work is handed over, so the worker's
    "running" cannot be overwritten by a late "queued"."""
    run_id = _run()
    jobs.submit(run_id, lambda: gate.wait(10))
    assert _wait_for(lambda: run_manager.get_run(run_id)["status"] == "running")
    assert run_manager.get_run(run_id)["queue_position"] == 0


def test_a_waiting_job_reports_its_place_in_the_queue(gate):
    def work():
        gate.wait(10)

    for _ in range(jobs.CONCURRENCY):
        jobs.submit(_run(), work)
    first, second = _run(), _run()
    jobs.submit(first, work)
    jobs.submit(second, work)

    assert run_manager.get_run(first)["status"] == "queued"
    assert run_manager.get_run(first)["queue_position"] == 1
    assert run_manager.get_run(second)["queue_position"] == 2
    assert "queued" in run_manager.get_run(second)["step"]


# -- cancelling ---------------------------------------------------------------


def test_a_queued_job_can_be_dropped_before_it_runs(gate):
    started: list[str] = []

    def work(run_id):
        started.append(run_id)
        gate.wait(10)

    for _ in range(jobs.CONCURRENCY):
        jobs.submit(_run(), work, "filler")
    waiting = _run()
    jobs.submit(waiting, work, waiting)

    assert jobs.cancel(waiting) is True
    run_manager.request_stop(waiting)
    assert run_manager.get_run(waiting)["status"] == "stopped"

    gate.set()
    time.sleep(0.5)
    assert waiting not in started      # it never ran, so no browser was started


def test_a_running_job_cannot_be_cancelled_that_way(gate):
    """Its thread is inside the scraper; the caller falls back to the
    cooperative stop, which is what the endpoint does."""
    run_id = _run()
    jobs.submit(run_id, lambda: gate.wait(10))
    assert _wait_for(lambda: run_manager.get_run(run_id)["status"] == "running")
    assert jobs.cancel(run_id) is False


def test_cancelling_an_unknown_run_is_harmless():
    assert jobs.cancel("nope") is False


# -- failure is contained -----------------------------------------------------


def test_a_job_that_raises_frees_its_slot():
    def boom():
        raise RuntimeError("scraper exploded")

    run_id = _run()
    jobs.submit(run_id, boom)
    assert _wait_for(lambda: jobs.stats()["running"] == 0)
    # …and the pool still accepts work afterwards.
    done = threading.Event()
    jobs.submit(_run(), done.set)
    assert done.wait(5)


# -- log tails ----------------------------------------------------------------


def test_each_runs_log_lines_land_in_its_own_tail(caplog):
    # The app's entry point sets the root logger to INFO; do the same here, or
    # nothing below WARNING ever reaches a handler (see run_logs.install).
    caplog.set_level(logging.INFO)
    run_logs.install()
    ids = [_run(), _run()]
    finished = threading.Event()

    def work(run_id, marker):
        logging.getLogger("app.demo").info("hello from %s", marker)
        if marker == "second":
            finished.set()

    for run_id, marker in zip(ids, ("first", "second")):
        jobs.submit(run_id, work, run_id, marker)
    assert finished.wait(5)
    time.sleep(0.2)

    first = " ".join(line["message"] for line in run_logs.tail(ids[0]))
    second = " ".join(line["message"] for line in run_logs.tail(ids[1]))
    assert "first" in first and "second" not in first
    assert "second" in second and "first" not in second


def test_the_tail_is_incremental_so_a_poller_only_gets_what_is_new():
    run_id = _run()
    run_logs.bind(run_id)
    try:
        for n in range(3):
            run_logs.record(run_id, "INFO", f"line {n}")
    finally:
        run_logs.unbind()

    everything = run_logs.tail(run_id)
    assert [line["message"] for line in everything] == ["line 0", "line 1", "line 2"]
    assert [line["message"] for line in run_logs.tail(run_id, after=everything[0]["seq"])] == [
        "line 1", "line 2",
    ]
    assert run_logs.tail(run_id, after=everything[-1]["seq"]) == []


def test_a_tail_is_bounded_so_a_long_run_cannot_grow_without_limit():
    run_id = _run()
    for n in range(run_logs.TAIL + 50):
        run_logs.record(run_id, "INFO", f"line {n}")
    lines = run_logs.tail(run_id)
    assert len(lines) == run_logs.TAIL
    assert lines[-1]["message"] == f"line {run_logs.TAIL + 49}"   # newest kept


def test_lines_logged_outside_a_run_are_not_attributed_to_one():
    run_logs.install()
    run_logs.unbind()
    logging.getLogger("app.demo").info("a request handler logging something")
    assert run_logs.current() is None
