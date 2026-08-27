"""Shared Selenium machinery for all portal scrapers.

Subclass BaseScraper and implement the portal-specific flow. The base handles
the Chrome driver, a per-run staging download directory, download completion,
failure screenshots, and step/status reporting via run_manager.
"""

import logging
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import (
    InvalidSessionIdException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from urllib3.exceptions import HTTPError as _Urllib3HTTPError
from webdriver_manager.chrome import ChromeDriverManager

from app.config import settings
from app.core import checkpoints, live, run_manager
from app.core.filenames import sanitize_filename

logger = logging.getLogger(__name__)

# Default element wait. Generous because these portals are slow under load and
# a scraper that gives up early loses a whole search pass; callers that need a
# tighter bound (a probe that expects to fail) pass their own.
WAIT_TIMEOUT = 60
# A single document. Public bid packages routinely include multi-hundred-MB
# drawing sets, which take minutes on a slow portal.
DOWNLOAD_TIMEOUT = 300

# Chrome network errors that are worth retrying: a transient DNS/socket failure
# rather than a real "this page is wrong" problem. DNS is the common one — a
# resolver that round-robins across upstreams (systemd-resolved does) will fail
# a lookup on a broken upstream and succeed on the next attempt via a good one.
TRANSIENT_NET_ERRORS = (
    "err_name_not_resolved",
    "err_name_resolution_failed",
    "err_dns_timed_out",
    "err_internet_disconnected",
    "err_connection_reset",
    "err_connection_closed",
    "err_connection_timed_out",
    "err_connection_failed",
    "err_timed_out",
    "err_empty_response",
    "err_socket_not_connected",
    "err_address_unreachable",
    "err_network_changed",
)
NAVIGATE_ATTEMPTS = 4      # total tries before giving up
NAVIGATE_BACKOFF = 2.0     # seconds, doubled after each failed attempt


class StopRequested(Exception):
    """Raised inside a scrape when the user has asked it to stop, so the flow
    unwinds at the next checkpoint instead of running to completion."""

# A realistic desktop-Chrome UA. Under --headless=new the default UA no longer
# leaks a "HeadlessChrome" token, but some portals (e.g. BidNet Direct) still
# 403 the automation fingerprint, so we pin a normal UA to match.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


def _clear_stale_wdm_locks(max_age: float = 300) -> None:
    """Remove leftover webdriver-manager lock files older than `max_age` seconds.

    webdriver-manager serialises driver downloads with a lock file under ~/.wdm.
    A process killed mid-install leaves that lock behind, and every later
    start_driver then waits on it and fails with "Timed out waiting for
    webdriver-manager lock". A healthy install holds the lock for seconds, so
    anything old is safe to clear.
    """
    try:
        for lock in (Path.home() / ".wdm").glob(".wdm-lock*"):
            if time.time() - lock.stat().st_mtime > max_age:
                lock.unlink(missing_ok=True)
                logger.warning("removed stale webdriver-manager lock %s", lock)
    except OSError:  # noqa: PERF203 — lock cleanup is best-effort
        pass


def _resolve_chromedriver() -> str:
    """ChromeDriverManager().install(), self-healing a stale wdm lock.

    Clears clearly-stale locks first; if the resolver still times out on the
    lock, remove it outright and retry once (a concurrent healthy install only
    holds the lock briefly, so a timeout means it is orphaned).
    """
    _clear_stale_wdm_locks()
    try:
        return ChromeDriverManager().install()
    except Exception as exc:  # noqa: BLE001 — only the lock timeout is retried
        if "lock" not in str(exc).lower():
            raise
        logger.warning("webdriver-manager lock timeout — clearing the lock and retrying once")
        _clear_stale_wdm_locks(max_age=0)
        return ChromeDriverManager().install()


class BaseScraper:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.run_dir = run_manager.run_folder(run_id)
        # Staging dir for browser downloads; files are moved out after each finishes.
        self.download_dir = self.run_dir / "_downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.driver: webdriver.Chrome | None = None
        # Last step reported via set_step — used to say where a failure happened.
        self.current_step: str | None = None
        # Flipped by stop() (from another thread) when the user asks to stop.
        self._stop_requested = False
        # Set once the browser has been torn down by stop(): nothing new may be
        # sent to it, though `self.driver` stays put so in-flight Selenium calls
        # still fail as WebDriverException rather than AttributeError.
        self._driver_closed = False

    # -- lifecycle ----------------------------------------------------------

    def start_driver(self, headless: bool | None = None, user_data_dir: str | None = None) -> None:
        """Launch Chrome. When `headless` is omitted, the run's own `live_preview`
        flag decides visibility: a run started from the "Live preview" button
        shows the browser, every other run is headless. An explicit `headless`
        argument overrides that (a portal that needs a human to solve a challenge
        forces it False). `user_data_dir` points Chrome at a persistent profile so
        cookies/session survive between runs."""
        options = Options()
        if headless is None:
            # Default: hidden, unless this run was launched as a live preview.
            run = run_manager.get_run(self.run_id) or {}
            headless = not run.get("live_preview", False)
        use_headless = headless
        if use_headless:
            options.add_argument("--headless=new")
        if user_data_dir:
            options.add_argument(f"--user-data-dir={user_data_dir}")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        if use_headless:
            options.add_argument("--window-size=1920,1080")
        else:
            # A visible window is being watched, so give it the whole screen:
            # BidNet's sidebar filters and date pickers sit below the fold at
            # 1920x1080 with browser chrome taking a slice off the top.
            options.add_argument("--start-maximized")
        # Trim Chrome's memory/CPU footprint. These portals are plain form-and-
        # table pages that need none of the GPU stack, extensions, or background
        # services, and on a memory-tight host that headroom is the difference
        # between a run finishing and Chrome being OOM-killed mid-flow (which
        # surfaces as a dead WebDriver session — see _driver_error_detail).
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-default-apps")
        # Resolve hostnames over DNS-over-HTTPS rather than through the machine's
        # resolver, which on some networks answers whole TLDs (.app, .dev) with an
        # empty record set and breaks a run with net::ERR_NAME_NOT_RESOLVED.
        # "secure" means DoH only — no silent fall back to the broken resolver.
        if settings.dns_over_https and settings.dns_over_https_templates.strip():
            options.add_argument("--dns-over-https-mode=secure")
            options.add_argument(
                f"--dns-over-https-templates={settings.dns_over_https_templates.strip()}"
            )
        # Return from driver.get() at DOMContentLoaded instead of the load event.
        # These portals pull third-party subresources (fonts, analytics) that can
        # stall for a minute on a flaky network, and waiting for the load event
        # times the renderer out even though the page itself is ready and usable.
        # Every flow here waits for the elements it needs anyway.
        options.page_load_strategy = "eager"
        # Strip the automation fingerprint that makes bot-protected portals return
        # 403 Forbidden: drop the "controlled by automated software" switches and
        # the AutomationControlled blink feature (which sets navigator.webdriver).
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(f"--user-agent={USER_AGENT}")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_experimental_option(
            "prefs",
            {
                "download.default_directory": str(self.download_dir),
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
                # A bid detail page downloads several attachments in a row; without
                # this Chrome silently blocks every download after the first.
                "profile.default_content_setting_values.automatic_downloads": 1,
            },
        )
        service = Service(_resolve_chromedriver())
        self.driver = webdriver.Chrome(service=service, options=options)
        self.driver.set_page_load_timeout(60)
        # Expose this run's browser to the shared live-screenshot endpoint so the
        # Live Preview modal can stream frames while it is open.
        live.register(self.run_id, self)
        # Belt-and-suspenders: ensure navigator.webdriver is undefined on every
        # document before the page's own scripts run, so bot checks don't see it.
        try:
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"},
            )
        except WebDriverException:
            pass

    def stop_driver(self) -> None:
        if self.driver and self._driver_closed:
            # stop() already quit it; calling quit again just retries against a
            # socket that is gone.
            self.driver = None
            return
        if self.driver:
            try:
                self.driver.quit()
            except Exception:  # noqa: BLE001 — a dead session raises urllib3 errors, not WebDriverException
                pass
            self.driver = None

    def cleanup(self) -> None:
        live.unregister(self.run_id)
        shutil.rmtree(self.download_dir, ignore_errors=True)
        self.stop_driver()

    def get_screenshot_base64(self) -> str | None:
        """A base64 PNG of the current browser view, or None. Used by the shared
        live-screenshot endpoint; defensive so a frame grab never breaks a run."""
        if not self.driver or self._driver_closed:
            return None
        try:
            return self.driver.get_screenshot_as_base64()
        except Exception:  # noqa: BLE001 — a dead session raises urllib3 errors; a frame grab must never break a run
            return None

    # -- helpers ------------------------------------------------------------

    def wait(self, timeout: int = WAIT_TIMEOUT) -> WebDriverWait:
        return WebDriverWait(self.driver, timeout)

    def navigate(
        self,
        url: str,
        attempts: int = NAVIGATE_ATTEMPTS,
        backoff: float = NAVIGATE_BACKOFF,
    ) -> None:
        """driver.get(url), retrying transient network/DNS failures.

        A single failed lookup is not proof the site is unreachable: a resolver
        that rotates across several upstreams returns NXDOMAIN/NODATA whenever it
        lands on a broken one and the correct answer on the next try. Retrying
        with backoff turns that intermittent failure into a successful run.

        Non-transient WebDriver errors are re-raised immediately — only the
        errors in TRANSIENT_NET_ERRORS are worth a second attempt. If every
        attempt fails we raise with the host name and the underlying Chrome
        error, so the cause is obvious instead of a bare stack trace.
        """
        delay = backoff
        last_exc: WebDriverException | None = None
        for attempt in range(1, attempts + 1):
            self.raise_if_stopped()  # don't start/retry a load the user has cancelled
            try:
                self.driver.get(url)
                if attempt > 1:
                    logger.info(
                        "[run %s] navigation to %s succeeded on attempt %d",
                        self.run_id, url, attempt,
                    )
                return
            except WebDriverException as exc:
                message = (getattr(exc, "msg", None) or str(exc)).lower()
                if not any(err in message for err in TRANSIENT_NET_ERRORS):
                    raise  # a real navigation error — don't mask it behind retries
                last_exc = exc
                if attempt == attempts:
                    break
                logger.warning(
                    "[run %s] transient network error loading %s (attempt %d/%d) — "
                    "retrying in %.1fs", self.run_id, url, attempt, attempts, delay,
                )
                time.sleep(delay)
                delay *= 2

        host = urlparse(url).hostname or url
        detail = (getattr(last_exc, "msg", None) or str(last_exc)).strip().splitlines()[0]
        raise WebDriverException(
            f"Could not reach {host} after {attempts} attempts — {detail}. "
            f"This is a network/DNS problem rather than a portal change: check "
            f"that this machine can resolve {host} (some ISP resolvers return an "
            f"empty answer for certain domains; a public resolver such as 8.8.8.8 "
            f"or 1.1.1.1 fixes that)."
        ) from last_exc

    # A dead browser (Chrome killed by the OOM reaper, a hard renderer crash, or
    # the Live-Preview window being closed) doesn't surface as a clean Selenium
    # error: the command socket to chromedriver is simply gone, so the call
    # raises a urllib3 connection error instead. Match those signatures anywhere
    # in the exception chain so the run reports a clear cause instead of a bare
    # "Connection refused" / MaxRetryError stack trace.
    _DEAD_SESSION_SIGNATURES = (
        "connection refused",
        "failed to establish a new connection",
        "max retries exceeded",
        "chrome not reachable",
        "no such session",
        "invalid session id",
        "session deleted because of page crash",
        "disconnected: not connected to devtools",
        "unable to connect to renderer",
    )

    @classmethod
    def _is_dead_session_error(cls, exc: BaseException) -> bool:
        """True if `exc` (or anything it wraps) means the browser process is gone."""
        seen: set[int] = set()
        cur: BaseException | None = exc
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            if isinstance(cur, (InvalidSessionIdException, _Urllib3HTTPError)):
                return True
            text = (getattr(cur, "msg", None) or str(cur)).lower()
            if any(sig in text for sig in cls._DEAD_SESSION_SIGNATURES):
                return True
            cur = cur.__cause__ or cur.__context__
        return False

    @classmethod
    def describe_failure(cls, exc: BaseException) -> str:
        """A human-readable cause for a run failure, cleaned up for a dead browser.

        For an OOM/crash the raw exception is an unhelpful connection-refused
        stack; return an actionable message instead. Everything else passes
        through as its first line, trimmed for the run's error field.
        """
        if cls._is_dead_session_error(exc):
            return (
                "The browser was terminated mid-run — most likely the machine ran "
                "out of memory (Chrome was OOM-killed), or the Live-Preview window "
                "was closed. Free up RAM (close other browsers / stale dev servers) "
                "and run it again."
            )
        return (str(getattr(exc, "msg", None) or exc).strip().splitlines() or [""])[0][:500]

    # -- stop support -------------------------------------------------------

    def stop(self) -> None:
        """Interrupt this run from another thread (the stop endpoint).

        Sets the cooperative flag and quits the browser: quitting makes whatever
        Selenium call the worker is blocked on (a page load, a WebDriverWait)
        raise at once, so the run unwinds immediately rather than only at the
        next checkpoint. Safe to call more than once.
        """
        self._stop_requested = True
        # Mark the browser unusable rather than clearing `self.driver`. Clearing
        # it would turn whatever Selenium call the worker is mid-way through
        # into an AttributeError, which the scrapers' `except WebDriverException`
        # handlers do not catch — a clean stop would surface as a crash. The
        # reference stays; this flag is what stops anything *new* being sent to
        # a chromedriver that is going away, which is where the trail of urllib3
        # "Connection refused" retries after a stop came from.
        self._driver_closed = True
        driver = self.driver
        if driver is not None:
            try:
                driver.quit()
            except Exception:  # noqa: BLE001 — the driver may already be gone
                pass

    def raise_if_stopped(self) -> None:
        """Checkpoint: park while paused, and raise StopRequested on a stop.

        Every scraper already calls this at its safe points — each step, each
        navigate attempt, each record — which is exactly the set of places it is
        safe to *hold* as well as to abandon. So pause needs no new call sites in
        any of the eight portals: the seam that was cut for stopping is the seam
        pausing wants, and a run pauses between records rather than in the middle
        of one because that is where these calls already sit.

        A pause holds the thread and the browser. That is deliberate: it is what
        makes resuming exact — the worker is still standing where it stopped, so
        it continues at the next record with nothing replayed and nothing
        collected twice. The cost is that a paused run keeps its concurrency slot
        (`jobs.stats` reports paused runs separately so the console can say so);
        what it gives back is the network and the CPU, which is the thing someone
        pausing a long run to get an urgent one out is actually short of.

        Stop is checked *first and again after*: a run that is paused and then
        stopped must not have to be resumed before it can be abandoned.
        """
        if self._stop_requested or run_manager.is_stop_requested(self.run_id):
            raise StopRequested("run stopped by user")

        if run_manager.is_paused(self.run_id):
            checkpoints.save(self.run_id)
            logger.info("[run %s] paused — holding at %s", self.run_id, self.step or "checkpoint")
            while run_manager.await_resume(self.run_id):
                # Bounded waits, so a stop lands on a parked worker promptly
                # rather than waiting for a resume that may never come.
                if self._stop_requested or run_manager.is_stop_requested(self.run_id):
                    raise StopRequested("run stopped by user")
            logger.info("[run %s] resumed", self.run_id)

        if self._stop_requested or run_manager.is_stop_requested(self.run_id):
            raise StopRequested("run stopped by user")

    # -- checkpointing ------------------------------------------------------

    def begin_checkpoint(self, scraper: str = "") -> None:
        """Open this run's checkpoint, adopting a resumed one if there is one."""
        checkpoints.start(self.run_id, scraper or self.__class__.__name__)

    def note_record(self, identifier: str, *, flush: bool = False, **position: object) -> None:
        """Mark one record extracted, so a resume never collects it twice.

        `identifier` is whatever that portal identifies a record by — a
        solicitation id, an ad number, a detail URL. Membership, not a count: a
        count is only enough if the portal returns the same rows in the same
        order on the way back in, and none of these portals promise that.
        """
        checkpoints.record(
            self.run_id, identifier=identifier, flush=flush,
            position=position or None,
        )

    def already_done(self, identifier: str) -> bool:
        """True when a resumed run has already extracted this record."""
        checkpoint = checkpoints.get(self.run_id)
        return bool(checkpoint and identifier in checkpoint.processed)

    def scroll_into_view(self, element) -> None:
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)

    def screenshot(self, name: str) -> None:
        # Nothing to capture once the browser has been stopped, and asking costs
        # three urllib3 retries against a closed socket.
        if self.driver and not self._driver_closed:
            try:
                self.driver.save_screenshot(str(self.run_dir / f"error_{sanitize_filename(name)}.png"))
            except Exception:  # noqa: BLE001 — never let a failure screenshot (esp. on a dead session) mask the real error
                pass

    def set_step(self, step: str) -> None:
        # Every step boundary is a natural stop checkpoint — and it covers the
        # window before the browser is even up, when stop() can't interrupt.
        self.raise_if_stopped()
        logger.info("[run %s] %s", self.run_id, step)
        self.current_step = step
        run_manager.update_run(self.run_id, step=step)

    def wait_for_download(
        self, timeout: int = DOWNLOAD_TIMEOUT, ignore: set[Path] | None = None
    ) -> Path:
        """Wait for a new file to fully land in the staging download dir.

        Chrome marks an in-progress download with a `.crdownload` suffix or, on
        Linux, a hidden `.com.google.Chrome.XXXXXX` temp name — a file is only
        finished once neither pattern is present.

        `ignore` is what was already staged before the click. Pass it when
        downloading several files in a row: without it this returns the newest
        file present, which — in the moment between the click and Chrome opening
        its `.crdownload` — is still the *previous* download. That returns
        immediately, the same file is claimed twice, and the run ends with fewer
        documents than the page offered.
        """
        ignore = ignore or set()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.raise_if_stopped()  # don't keep waiting on a download the user cancelled
            partial = [
                f for f in self.download_dir.iterdir()
                if f.is_file() and (f.suffix == ".crdownload" or f.name.startswith(".com.google.Chrome."))
            ]
            done = [
                f for f in self.download_dir.iterdir()
                if f.is_file() and f.suffix != ".crdownload"
                and not f.name.startswith(".com.google.Chrome.")
                and f not in ignore
            ]
            if done and not partial:
                return max(done, key=lambda f: f.stat().st_mtime)
            time.sleep(0.5)
        raise TimeoutException(f"Download did not complete within {timeout}s")
