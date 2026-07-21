"""Selenium automation for the Cal eProcure supplier portal.

Cal eProcure (California eProcurement, BidSync "BS3") is an ASP.NET site. The
supplier login page exposes a username field (#userid) and a password field
(#pwd); auth is a plain username + password (no SSO/MFA/CAPTCHA).

STATUS — login milestone only. This module currently signs in and verifies the
session, then captures where it landed (URL, page title, a screenshot, and the
landing page's HTML) so the post-login flow — navigation, search, per-bid
fields, document downloads — can be designed against the real page. The search
and scrape steps, the DB models, and the Excel export are added next, following
the SEPTA/North Dakota pattern (runs + bids, DB-first with an Excel fallback).
"""

import logging
from datetime import datetime
from typing import Any

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC

from app.config import settings
from app.core import run_manager
from app.core.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

LOGIN_URL = "https://caleprocure.ca.gov/pages/BS3/login.aspx"
LOGIN_REDIRECT_WAIT = 20  # seconds to wait for the post-login redirect

# The login page marks the token that stays in the URL while unauthenticated.
# Leaving it behind is the primary "we're signed in" signal.
LOGIN_URL_MARKER = "login.aspx"

# Login button: the page HTML we have only covers the two inputs, so try a
# range of common submit shapes and fall back to pressing Enter in the password
# field. If the click never leaves the login page we surface a clear error.
LOGIN_BUTTON_SELECTORS: list[tuple[str, str]] = [
    (By.ID, "login"),
    (By.ID, "loginButton"),
    (By.ID, "btnLogin"),
    (By.CSS_SELECTOR, "button[type='submit']"),
    (By.CSS_SELECTOR, "input[type='submit']"),
    (By.XPATH, "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'log in')]"),
    (By.XPATH, "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'login')]"),
    (By.XPATH, "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'sign in')]"),
    (By.XPATH, "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'login')]"),
    (By.XPATH, "//input[@value='Login' or @value='LOGIN' or @value='Sign In' or @value='SIGN IN' or @value='Submit']"),
]

# Text that betrays a failed sign-in still sitting on the login page.
LOGIN_ERROR_XPATH = (
    "//*[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'invalid') "
    "or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'incorrect') "
    "or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'failed') "
    "or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'not recognized')]"
)


class CalEProcureScraper(BaseScraper):
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

    def _find_login_button(self):
        for by, selector in LOGIN_BUTTON_SELECTORS:
            for candidate in self.driver.find_elements(by, selector):
                try:
                    if candidate.is_displayed() and candidate.is_enabled():
                        return candidate
                except WebDriverException:
                    continue
        return None

    def _login_error_text(self) -> str:
        for el in self.driver.find_elements(By.XPATH, LOGIN_ERROR_XPATH):
            try:
                text = (el.text or "").strip()
                if text:
                    return text[:200]
            except WebDriverException:
                continue
        return ""

    # -- login --------------------------------------------------------------

    def login(self) -> None:
        self.set_step("logging_in")

        if not settings.cal_eprocure_username or not settings.cal_eprocure_password:
            raise WebDriverException(
                "Cal eProcure credentials are empty — set Cal_ePROCURE_USERNAME and "
                "Cal_ePROCURE_PASSWORD in server/.env, then start the run again."
            )

        url = settings.cal_eprocure_link or LOGIN_URL
        logger.info("[run %s] navigating to %s", self.run_id, url)
        self.driver.get(url)

        try:
            user_field = self.wait(LOGIN_REDIRECT_WAIT).until(
                EC.presence_of_element_located((By.ID, "userid"))
            )
        except TimeoutException:
            self.screenshot("login_no_userid")
            raise WebDriverException(
                "Cal eProcure login: the username field (#userid) never appeared — "
                "the login page may have changed or failed to load."
            )
        try:
            pwd_field = self.driver.find_element(By.ID, "pwd")
        except WebDriverException:
            self.screenshot("login_no_pwd")
            raise WebDriverException("Cal eProcure login: the password field (#pwd) was not found.")

        user_field.clear()
        user_field.send_keys(settings.cal_eprocure_username)
        pwd_field.clear()
        pwd_field.send_keys(settings.cal_eprocure_password)

        button = self._find_login_button()
        if button is not None:
            logger.info("[run %s] clicking login button", self.run_id)
            self._safe_click(button)
        else:
            # No recognizable submit control — submit the form via the keyboard.
            logger.info("[run %s] no login button matched; submitting with Enter", self.run_id)
            pwd_field.send_keys(Keys.RETURN)

        # Success = we leave the login page (or the password field goes stale as
        # the app navigates away).
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
                "Cal eProcure login did not complete — still on the login page. "
                "Check the credentials in server/.env, or the portal may need a "
                f"login button selector I don't have yet.{detail}"
            )

        logger.info("[run %s] login successful; landed on %s", self.run_id, self.driver.current_url)

    # -- orchestration ------------------------------------------------------

    def run(self) -> None:
        run_manager.update_run(self.run_id, status="running")
        try:
            self.start_driver()
            self.login()

            # Login milestone: capture the post-login landing so the real flow
            # (navigation + search + fields) can be designed against it. Once the
            # flow is known this block is replaced by navigate/search/scrape +
            # DB persistence + Excel, exactly like the SEPTA scraper.
            landing_url = self.driver.current_url
            landing_title = (self.driver.title or "").strip()
            self.screenshot("post_login")
            try:
                (self.run_dir / "landing.html").write_text(
                    self.driver.page_source, encoding="utf-8"
                )
            except Exception:  # noqa: BLE001 — the diagnostic dump is best-effort
                logger.info("[run %s] could not save landing.html", self.run_id)

            logger.info(
                "[run %s] login verified — landed on %r (%s)",
                self.run_id, landing_title, landing_url,
            )
            run_manager.update_run(
                self.run_id,
                status="completed",
                step="login_verified",
                login_ok=True,
                landing_url=landing_url,
                landing_title=landing_title,
            )
            run_manager.add_warning(
                self.run_id,
                "Login succeeded. The post-login scraping flow is not built yet — "
                "this run only verifies sign-in and records where it landed "
                "(see the screenshot and landing.html in the run folder).",
            )
        except Exception as exc:  # noqa: BLE001 — a failed run must be reported, not crash the worker
            logger.exception("[run %s] failed", self.run_id)
            self.screenshot("fatal")
            run_manager.add_error(self.run_id, str(exc)[:500])
            run_manager.update_run(self.run_id, status="failed", step="failed")
        finally:
            self.cleanup()
            run_manager.update_run(self.run_id, finished_at=datetime.now().isoformat())


def execute_run(run_id: str) -> None:
    CalEProcureScraper(run_id).run()
