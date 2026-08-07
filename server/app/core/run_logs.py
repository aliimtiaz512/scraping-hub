"""Per-run log tails, collected without any scraper knowing about them.

A run owns one worker thread for its lifetime (see `app.core.jobs`), so the
thread is a reliable stand-in for "which run is this?". The worker binds its
run id to the thread; a handler on the root logger then attributes every record
emitted beneath it — from the scraper, the engine, urllib3, anything — to that
run, and keeps the last `TAIL` of them.

The alternative would have been to thread a run id through every logging call in
eleven scrapers, or to parse the "[run abc123]" prefix the modules happen to
write. This needs neither: nothing in a scraper changes, and a module that never
mentions a run id still has its lines captured.

Known gap, stated rather than hidden: a scraper that spawns its own inner
threads (SAM's engine does) loses attribution for whatever those threads log,
because the binding is per-thread. Those lines still reach the normal log; they
just do not appear in that run's tail.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any

#: Lines kept per run. Enough to see what a run has been doing without holding
#: a whole run's output in memory for every run in the registry.
TAIL = 200

_local = threading.local()
_lock = threading.Lock()
# run_id -> deque of {seq, ts, level, logger, message}
_tails: dict[str, deque] = {}
_sequence: dict[str, int] = {}


def bind(run_id: str) -> None:
    """Attribute this thread's log records to `run_id`."""
    _local.run_id = run_id
    with _lock:
        _tails.setdefault(run_id, deque(maxlen=TAIL))
        _sequence.setdefault(run_id, 0)


def unbind() -> None:
    _local.run_id = None


def current() -> str | None:
    return getattr(_local, "run_id", None)


def record(run_id: str, level: str, message: str, logger_name: str = "") -> None:
    """Append one line to a run's tail."""
    with _lock:
        tail = _tails.setdefault(run_id, deque(maxlen=TAIL))
        _sequence[run_id] = _sequence.get(run_id, 0) + 1
        tail.append({
            "seq": _sequence[run_id],
            "ts": None,  # filled by the handler; kept here for direct callers
            "level": level,
            "logger": logger_name,
            "message": message,
        })


def tail(run_id: str, after: int = 0) -> list[dict[str, Any]]:
    """This run's log lines with `seq` greater than `after`, oldest first.

    The console polls with the last seq it saw, so each poll carries only what
    is new rather than the whole buffer.
    """
    with _lock:
        lines = list(_tails.get(run_id, ()))
    return [line for line in lines if line["seq"] > after]


def latest_seq(run_id: str) -> int:
    with _lock:
        return _sequence.get(run_id, 0)


def forget(run_id: str) -> None:
    """Drop a finished run's tail (called when its state is evicted)."""
    with _lock:
        _tails.pop(run_id, None)
        _sequence.pop(run_id, None)


class RunLogHandler(logging.Handler):
    """Routes each record to the tail of whichever run's thread emitted it."""

    def emit(self, log_record: logging.LogRecord) -> None:
        run_id = current()
        if not run_id:
            return
        try:
            message = log_record.getMessage()
        except Exception:  # noqa: BLE001 — a bad format string must not break logging
            return
        with _lock:
            entries = _tails.setdefault(run_id, deque(maxlen=TAIL))
            _sequence[run_id] = _sequence.get(run_id, 0) + 1
            entries.append({
                "seq": _sequence[run_id],
                "ts": log_record.created,
                "level": log_record.levelname,
                "logger": log_record.name,
                "message": message[:2000],
            })


_installed = False


def install() -> None:
    """Attach the handler to the root logger once, at startup.

    Capture depends on the root logger's own level: a record below it never
    reaches any handler, so a root at WARNING yields empty tails however this
    is configured. The app sets INFO in `main.py`; an entry point that does not
    (a bare script, a test) has to do the same to see tails. Deliberately not
    forced here — how loud the logs are is the application's call, not this
    module's.
    """
    global _installed
    if _installed:
        return
    handler = RunLogHandler()
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    _installed = True
