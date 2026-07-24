"""Selenium automation for EMMA (eMaryland Marketplace Advantage).

EMMA is Maryland's procurement portal on the Ivalua platform — the same product
behind North Dakota's ND Buys (every control carries data-iv-* attributes).
Unlike ND, the login is a plain form directly on the page: Email/Username
(#body_x_txtLogin), Password (#body_x_txtPass) and a Log in submit button
(#body_x_btnLogin) — no OAuth/B2C redirect, so no CAPTCHA interception either.
The page encrypts the password into a hidden `crypted_pass` input on submit,
which is why the real Log in button must be clicked (never a bare form.submit).

STATUS — login + navigation milestone. This module signs in, opens the
"Sourcing" nav dropdown and clicks "Public Solicitations"
(/page.aspx/en/rfp/request_browse_public), then captures the list page (URL,
title, a screenshot, and the page HTML) so the grid scraping — columns,
pagination, per-bid fields — can be designed against the real markup. The
scrape/persist/export steps are added next, following the SEPTA/North Dakota
pattern (runs + bids, DB-first with an Excel fallback).
"""

import logging
import time
from datetime import datetime

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC

from app.config import settings
from app.core import run_manager
from app.core.base_scraper import BaseScraper
from app.core.filenames import sanitize_filename

logger = logging.getLogger(__name__)

LOGIN_URL = "https://emma.maryland.gov/page.aspx/en/usr/login"
LOGIN_REDIRECT_WAIT = 30  # seconds to wait for the post-login redirect

# Ivalua login form controls, confirmed against the live page HTML.
USERNAME_ID = "body_x_txtLogin"   # placeholder "Email / Username"
PASSWORD_ID = "body_x_txtPass"
LOGIN_BTN_ID = "body_x_btnLogin"  # the "Log in" submit button

# The path token that stays in the URL while unauthenticated. Leaving it behind
# is the primary "we're signed in" signal.
LOGIN_URL_MARKER = "/usr/login"

# Text that betrays a failed sign-in still sitting on the login page. Ivalua
# surfaces auth errors in a notification block, so scan for the usual wording.
LOGIN_ERROR_XPATH = (
    "//*[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'invalid') "
    "or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'incorrect') "
    "or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'failed') "
    "or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'locked') "
    "or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'not recognized')]"
)

# -- post-login navigation ----------------------------------------------------
# Top-nav "Sourcing" dropdown (<button class="... menu-button">Sourcing</button>)
# opens a menu whose "Public Solicitations" item links to the browse page below.
SOURCING_MENU_TEXT = "Sourcing"
PUBLIC_SOLICITATIONS_TEXT = "Public Solicitations"
# The href of the menu item and the URL token that confirms we arrived.
PUBLIC_SOLICITATIONS_HREF = "request_browse_public"
# Ivalua browse pages render their results in a grid with this id (same control
# the ND Buys scraper reads); used as a soft "the list rendered" signal.
GRID_ID = "body_x_grid_grd"


class EmmaScraper(BaseScraper):
    def __init__(self, run_id: str):
        super().__init__(run_id)

    # -- helpers ------------------------------------------------------------

    def _safe_click(self, element) -> bool:
        """Click, falling back to a JS click if something overlays the target."""
        try:
            element.click()
            return True
        except WebDriverException:
            try:
                self.driver.execute_script("arguments[0].click();", element)
                return True
            except WebDriverException:
                return False

    def _login_error_text(self) -> str:
        for el in self.driver.find_elements(By.XPATH, LOGIN_ERROR_XPATH):
            try:
                text = (el.text or "").strip()
                if text:
                    return text[:200]
            except WebDriverException:
                continue
        return ""

    def _click_by_text(self, tags: list[str], text: str, timeout: int = 20) -> None:
        """Click the first *visible* element among `tags` whose text matches `text`.

        Ivalua menus render items as buttons/anchors/list items, ship a duplicate
        hidden responsive nav (so the same label exists off-screen), and wire up
        their click handlers a beat after the element appears. So rather than wait
        on `element_to_be_clickable` (which can latch onto the hidden duplicate and
        time out), we poll for a *displayed* match over the whole window, scroll it
        into view, and try a native click then a JS click (which still fires even
        if an overlay would intercept the hit).
        """
        conditions = " or ".join(f"self::{tag}" for tag in tags)
        xpath = (
            f"//*[({conditions})]"
            f"[contains(normalize-space(.), {_xpath_literal(text)})]"
        )
        deadline = time.monotonic() + timeout
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            try:
                elements = self.driver.find_elements(By.XPATH, xpath)
            except WebDriverException:
                elements = []
            for el in elements:
                try:
                    if not (el.is_displayed() and el.is_enabled()):
                        continue
                    self.scroll_into_view(el)
                    try:
                        el.click()
                    except WebDriverException:
                        self.driver.execute_script("arguments[0].click();", el)
                    return
                except WebDriverException as exc:
                    last_err = exc
                    continue
            time.sleep(0.5)

        # Nothing clickable turned up — capture what's actually on screen so the
        # page markup can be inspected.
        self.screenshot(f"click_failed_{text}")
        self._dump_page(f"click_failed_{text}")
        raise TimeoutException(
            f"could not click a visible '{text}' element within {timeout}s"
        ) from last_err

    def _dump_page(self, name: str) -> None:
        """Save the current page HTML into the run folder for debugging."""
        try:
            path = self.run_dir / f"page_{sanitize_filename(name)}.html"
            path.write_text(self.driver.page_source, encoding="utf-8")
            logger.info("[run %s] saved page HTML -> %s", self.run_id, path)
        except Exception:  # noqa: BLE001 — diagnostics must never break the run
            pass

    # -- login --------------------------------------------------------------

    def login(self) -> None:
        self.set_step("logging_in")

        if not settings.emma_username or not settings.emma_password:
            raise WebDriverException(
                "EMMA credentials are empty — set EMMA_USERNAME and "
                "EMMA_PASSWORD in server/.env, then start the run again."
            )

        url = settings.emma_link or LOGIN_URL
        logger.info("[run %s] navigating to %s", self.run_id, url)
        self.driver.get(url)

        try:
            user_field = self.wait(LOGIN_REDIRECT_WAIT).until(
                EC.presence_of_element_located((By.ID, USERNAME_ID))
            )
        except TimeoutException:
            self.screenshot("login_no_username")
            raise WebDriverException(
                "EMMA login: the username field (#body_x_txtLogin) never appeared — "
                "the login page may have changed or failed to load."
            )
        try:
            pwd_field = self.driver.find_element(By.ID, PASSWORD_ID)
        except WebDriverException:
            self.screenshot("login_no_pwd")
            raise WebDriverException(
                "EMMA login: the password field (#body_x_txtPass) was not found."
            )

        user_field.clear()
        user_field.send_keys(settings.emma_username)
        pwd_field.clear()
        pwd_field.send_keys(settings.emma_password)

        # Clicking the real Log in button matters here: the page's own submit
        # handler encrypts the password into the hidden crypted_pass input.
        try:
            button = self.wait(10).until(EC.element_to_be_clickable((By.ID, LOGIN_BTN_ID)))
            logger.info("[run %s] clicking the Log in button", self.run_id)
            self._safe_click(button)
        except TimeoutException:
            # Enter in the password field triggers the same submit button.
            logger.info("[run %s] Log in button not clickable; submitting with Enter", self.run_id)
            pwd_field.send_keys(Keys.RETURN)

        # Success = we leave the login page.
        try:
            self.wait(LOGIN_REDIRECT_WAIT).until(
                lambda d: LOGIN_URL_MARKER not in d.current_url.lower()
            )
        except TimeoutException:
            logger.warning("[run %s] no redirect away from the login page yet", self.run_id)

        if LOGIN_URL_MARKER in self.driver.current_url.lower():
            message = self._login_error_text()
            self.screenshot("login_failed")
            detail = f" Portal said: {message}" if message else ""
            raise WebDriverException(
                "EMMA login did not complete — still on the login page. "
                f"Check the credentials in server/.env.{detail}"
            )

        logger.info("[run %s] login successful; landed on %s", self.run_id, self.driver.current_url)

    # -- navigation ---------------------------------------------------------

    def open_public_solicitations(self) -> None:
        """Open the top-nav "Sourcing" dropdown and click "Public Solicitations"."""
        self.set_step("opening_sourcing_menu")
        self._click_by_text(["button"], SOURCING_MENU_TEXT, timeout=30)
        time.sleep(1)  # let the dropdown's open transition finish

        self.set_step("opening_public_solicitations")
        # Prefer the exact menu link by href — it's unambiguous — and fall back
        # to matching the visible label if the markup shifts.
        clicked = False
        for link in self.driver.find_elements(
            By.CSS_SELECTOR, f"a[href*='{PUBLIC_SOLICITATIONS_HREF}']"
        ):
            try:
                if link.is_displayed():
                    self.scroll_into_view(link)
                    if self._safe_click(link):
                        clicked = True
                        break
            except WebDriverException:
                continue
        if not clicked:
            self._click_by_text(["a", "button", "span", "li"], PUBLIC_SOLICITATIONS_TEXT, timeout=20)

        # Arrival = the browse page URL. The grid is a soft signal on top: wait
        # for it briefly so the capture below shows rendered rows, but don't fail
        # the run over it — the saved HTML is what the next stage is built from.
        try:
            self.wait(30).until(lambda d: PUBLIC_SOLICITATIONS_HREF in d.current_url.lower())
        except TimeoutException:
            self.screenshot("public_solicitations_not_reached")
            self._dump_page("public_solicitations_not_reached")
            raise WebDriverException(
                "EMMA navigation did not reach the Public Solicitations page — "
                "the Sourcing menu or its Public Solicitations item may have changed."
            )
        try:
            self.wait(20).until(EC.presence_of_element_located((By.ID, GRID_ID)))
        except TimeoutException:
            logger.warning(
                "[run %s] no #%s grid on the Public Solicitations page yet — "
                "capturing the page as-is", self.run_id, GRID_ID,
            )

        logger.info(
            "[run %s] Public Solicitations open at %s", self.run_id, self.driver.current_url
        )

    # -- orchestration ------------------------------------------------------

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        try:
            self.start_driver()
            self.login()
            self.open_public_solicitations()

            # Navigation milestone: capture the Public Solicitations list so the
            # grid scraping (columns, pagination, per-bid fields) can be designed
            # against the real markup. Once the grid is mapped this block is
            # replaced by scrape + DB persistence + Excel, exactly like the North
            # Dakota scraper.
            landing_url = self.driver.current_url
            landing_title = (self.driver.title or "").strip()
            self.screenshot("public_solicitations")
            self._dump_page("public_solicitations")

            logger.info(
                "[run %s] Public Solicitations reached — %r (%s)",
                self.run_id, landing_title, landing_url,
            )
            run_manager.update_run(
                self.run_id,
                status="completed",
                step="public_solicitations_open",
                login_ok=True,
                landing_url=landing_url,
                landing_title=landing_title,
            )
            run_manager.add_warning(
                self.run_id,
                "Login succeeded and the Public Solicitations page is open. The "
                "list scraping is not built yet — this run captures the page "
                "(see the screenshot and page_public_solicitations.html in the "
                "run folder) so the grid flow can be designed against it.",
            )
        except Exception as exc:  # noqa: BLE001 — a failed run must be reported, not crash the worker
            logger.exception("[run %s] failed", self.run_id)
            self.screenshot("fatal")
            run_manager.add_error(self.run_id, str(exc)[:500])
            run_manager.update_run(self.run_id, status="failed", step="failed")
        finally:
            self.cleanup()
            run_manager.update_run(self.run_id, finished_at=datetime.now().isoformat())


def _xpath_literal(text: str) -> str:
    """Quote a string for use inside an XPath expression."""
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    parts = text.split("'")
    return "concat('" + "', \"'\", '".join(parts) + "')"


def execute_run(run_id: str) -> None:
    EmmaScraper(run_id).run()
