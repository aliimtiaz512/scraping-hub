import time
import csv
import re
import os
from datetime import datetime
from typing import List, Dict, Set
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from dotenv import load_dotenv
import logging
from selenium.webdriver.common.keys import Keys

# Configuration
load_dotenv()
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/unison_scraper.log'),
        logging.StreamHandler()
    ]
)

class UnisonMarketplaceScraper:
    def __init__(self):
        self.driver = None
        # Browser visibility: headless by default (set False for a live preview).
        self.headless = True
        # Create new CSV file with timestamp for each run
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.csv_file = f'unison_requests_{timestamp}.csv'
        self.processed_ids = set()
        # Filled in by collect_listing: how many pages the walk covered, and how
        # many buys the listing said it held.
        self.pages_scraped = 0
        self.expected_buys = None
        # Whether the requested Filter By criterion actually took (see
        # collect_listing). True when none was requested.
        self.filter_applied = True
        self.keywords_to_exclude = [
            'gsa schedules', 'food rfi', 'market research', 
            'foods', 'meal', 'survey'
        ]
        self.base_url = 'https://marketplace.unisonglobal.com/fbweb/sellerDashboard.do'
        
    def setup_driver(self):
        """Initialize Chrome driver with options"""
        options = webdriver.ChromeOptions()
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        # Hidden by default; a live-preview run sets self.headless = False. A fixed
        # window size keeps the headless layout identical to the headed one so the
        # existing selectors/flow are unaffected.
        if self.headless:
            options.add_argument('--headless=new')
            options.add_argument('--window-size=1920,1080')

        # Remove automation flags to avoid detection
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)

        self.driver = webdriver.Chrome(options=options)
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        logging.info("WebDriver initialized successfully")

    def get_screenshot_base64(self):
        """A base64 PNG of the current browser view, or None. Used by the shared
        live-screenshot endpoint; defensive so a frame grab never breaks a run."""
        if not self.driver:
            return None
        try:
            return self.driver.get_screenshot_as_base64()
        except Exception:  # noqa: BLE001 — a failed frame must never affect the scrape
            return None
        
    def load_existing_data(self):
        """Load existing Buyer# from CSV to prevent duplicates"""
        if os.path.exists(self.csv_file):
            try:
                with open(self.csv_file, 'r', newline='', encoding='utf-8') as file:
                    reader = csv.DictReader(file)
                    for row in reader:
                        if 'Buyer#' in row and row['Buyer#']:
                            self.processed_ids.add(row['Buyer#'].strip())
                logging.info(f"Loaded {len(self.processed_ids)} existing records")
            except Exception as e:
                logging.warning(f"Could not read existing CSV: {e}")
    
    def check_terms_checkbox(self):
        """Check the 'I agree to comply with the Terms of Use' checkbox"""
        try:
            logging.info("Looking for Terms of Use checkbox...")
            
            # Multiple strategies to find the checkbox
            checkbox_selectors = [
                # Look for checkbox with associated text containing "I agree"
                "//input[@type='checkbox' and following-sibling::text()[contains(., 'I agree')]]",
                "//input[@type='checkbox' and following::*[contains(text(), 'I agree')]]",
                "//input[@type='checkbox' and preceding::*[contains(text(), 'I agree')]]",
                
                # Look for label containing "I agree" and find associated checkbox
                "//label[contains(text(), 'I agree')]/input[@type='checkbox']",
                "//label[contains(., 'I agree')]//input[@type='checkbox']",
                
                # Look for any checkbox near "Terms of Use" text
                "//*[contains(text(), 'Terms of Use')]/preceding-sibling::input[@type='checkbox']",
                "//*[contains(text(), 'Terms of Use')]/following-sibling::input[@type='checkbox']",
                
                # Generic checkbox selectors
                "//input[@type='checkbox' and contains(@id, 'agree')]",
                "//input[@type='checkbox' and contains(@name, 'agree')]",
                "//input[@type='checkbox' and contains(@id, 'terms')]",
                "//input[@type='checkbox' and contains(@name, 'terms')]",
            ]
            
            checkbox = None
            
            # FIRST: Check cheap/fast existence without waiting
            for selector in checkbox_selectors:
                elements = self.driver.find_elements(By.XPATH, selector)
                if elements:
                    checkbox = elements[0]
                    logging.info(f"Found checkbox immediately with selector: {selector}")
                    break
            
            # SECOND: If not found, wait briefly for the most likely one (generic fallback)
            if not checkbox:
                try:
                    # Fallback generic wait - catch-all
                    checkbox = WebDriverWait(self.driver, 2).until(
                        EC.presence_of_element_located((By.XPATH, "//input[@type='checkbox']"))
                    )
                except TimeoutException:
                    pass

            if checkbox:
                # Scroll the checkbox into view
                self.driver.execute_script("arguments[0].scrollIntoView(true);", checkbox)
                
                # Check if already selected
                if not checkbox.is_selected():
                    # Try clicking the checkbox directly
                    try:
                        checkbox.click()
                        logging.info("✓ Checkbox clicked successfully")
                    except Exception as click_error:
                        # If direct click fails, try JavaScript click
                        logging.warning(f"Direct click failed, trying JavaScript: {click_error}")
                        self.driver.execute_script("arguments[0].click();", checkbox)
                        logging.info("✓ Checkbox clicked via JavaScript")
                else:
                    logging.info("✓ Checkbox was already checked")
                
                return True
            
            logging.warning("Could not find Terms of Use checkbox. Proceeding without checking.")
            return False
                
        except Exception as e:
            logging.error(f"Error handling checkbox: {e}")
            return False
    
    def login(self):
        """Login to Unison Marketplace via Keycloak - UPDATED WITH CHECKBOX"""
        try:
            logging.info("Navigating to login page...")
            self.driver.get(self.base_url)
            
            # Optimized: Wait for username field instead of hard sleep
            try:
                # Primary ID check first - explicit wait
                email_field = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.ID, "username"))
                )
            except TimeoutException:
                # Fallback selectors checked rapidly
                email_selectors = [
                    (By.XPATH, "//input[@type='email']"),
                    (By.XPATH, "//input[contains(@name, 'email')]"),
                    (By.XPATH, "//input[@placeholder='Email Address']"),
                    (By.XPATH, "//input[@name='username']")
                ]
                email_field = None
                for by, selector in email_selectors:
                    elems = self.driver.find_elements(by, selector)
                    if elems:
                        email_field = elems[0]
                        break
                
                if not email_field:
                    raise Exception("Could not find email field")

            email_field.send_keys(os.getenv('UNISON_EMAIL', 'your_email@example.com'))
            logging.info("Entered email")
            
            # Find password field (ID should be "password")
            try:
                password_field = self.driver.find_element(By.ID, "password")
            except NoSuchElementException:
                # Fallback selectors
                password_selectors = [
                    (By.XPATH, "//input[@type='password']"),
                    (By.XPATH, "//input[contains(@name, 'password')]"),
                    (By.XPATH, "//input[@placeholder='Password']")
                ]
                password_field = None
                for by, selector in password_selectors:
                    elems = self.driver.find_elements(by, selector)
                    if elems:
                        password_field = elems[0]
                        break
            
            if password_field:
                password_field.send_keys(os.getenv('UNISON_PASSWORD', 'your_password'))
                logging.info("Entered password")
            else:
                 logging.error("Could not find password field")

            # NEW: Check the Terms of Use checkbox (Optimized version)
            self.check_terms_checkbox()
            
            # Find login button - check all selectors rapidly
            login_button = None
            login_button_selectors = [
                (By.XPATH, "//button[contains(text(), 'Login')]"),
                (By.XPATH, "//button[contains(text(), 'Log In')]"),
                (By.XPATH, "//input[@type='submit' and @value='Login']"),
                (By.XPATH, "//input[@type='submit' and contains(@value, 'Login')]"),
                (By.XPATH, "//button[@type='submit']"),
                (By.CSS_SELECTOR, "button.pf-c-button.pf-m-primary"),
                (By.XPATH, "//button[@id='kc-login']"),
            ]
            
            for by, selector in login_button_selectors:
                elems = self.driver.find_elements(by, selector)
                if elems:
                    # Check if visible/clickable
                    if elems[0].is_displayed() and elems[0].is_enabled():
                        login_button = elems[0]
                        logging.info(f"Found login button with selector: {selector}")
                        break

            if not login_button:
                logging.error("Could not find login button with any selector")
                # Try to press Enter on password field as fallback
                try:
                    if password_field:
                        password_field.send_keys(Keys.RETURN)
                        logging.info("Pressed Enter on password field as fallback")
                except:
                    pass
            else:
                login_button.click()
                logging.info("Clicked login button")
            
            # Optimized: Wait for URL change or failure indicator
            logging.info("Waiting for login to complete...")
            try:
                WebDriverWait(self.driver, 15).until(
                    lambda d: "dashboard" in d.current_url.lower() or 
                              "sellerDashboard" in d.current_url or
                              "opportunities" in d.current_url
                )
                logging.info("✓ Login successful! On dashboard page.")
                return True
            except TimeoutException:
                # Check for error messages if timeout
                page_source = self.driver.page_source.lower()
                if "invalid" in page_source or "error" in page_source or "incorrect" in page_source:
                    logging.error("Login failed - invalid credentials or error message detected")
                    return False
                
                # Check if we are still on login page
                if "login" in self.driver.current_url.lower():
                     logging.warning("Still on login URL after wait.")
                     return False

                logging.info(f"Assuming successful login, current URL: {self.driver.current_url}")
                return True
            
        except Exception as e:
            logging.error(f"Login failed with error: {str(e)}")
            return False
    
    def navigate_to_requests_page(self):
        """Navigate to the page where requests are listed"""
        try:
            logging.info("Looking for requests/opportunities page...")
            
            # Try common navigation paths to find requests
            navigation_attempts = [
                # Try clicking on common menu items
                lambda: self.driver.find_element(By.XPATH, "//a[contains(text(), 'Opportunities')]").click(),
                lambda: self.driver.find_element(By.XPATH, "//a[contains(text(), 'Browse Opportunities')]").click(),
                lambda: self.driver.find_element(By.XPATH, "//a[contains(text(), 'Active Requests')]").click(),
                lambda: self.driver.find_element(By.XPATH, "//a[contains(text(), 'RFPs')]").click(),
                lambda: self.driver.find_element(By.XPATH, "//a[contains(text(), 'Bids')]").click(),
                lambda: self.driver.find_element(By.XPATH, "//a[contains(@href, 'opportunities')]").click(),
                lambda: self.driver.find_element(By.XPATH, "//a[contains(@href, 'requests')]").click(),
                # Try direct URL if we know it
                lambda: self.driver.get("https://marketplace.unisonglobal.com/fbweb/opportunities.do"),
                lambda: self.driver.get("https://marketplace.unisonglobal.com/fbweb/activeRequests.do"),
            ]
            
            for i, attempt in enumerate(navigation_attempts):
                try:
                    attempt()
                    logging.info(f"Navigation attempt {i+1} succeeded")
                    time.sleep(3)
                    return True
                except Exception as e:
                    continue
            
            logging.warning("Could not navigate to requests page. Staying on current page.")

            return True  # Continue anyway
            
        except Exception as e:
            logging.error(f"Navigation error: {e}")
            return True  # Continue anyway
    
    def apply_filter(self, filter_name: str = "Posted Today"):
        """Apply filter to requests. Default: 'Posted Today'"""
        try:
            logging.info(f"Looking for filter dropdown to apply: {filter_name}...")
            time.sleep(2)
            
            # Save screenshot before looking for filter

            
            # Try multiple selectors for filter dropdown
            filter_selectors = [
                "//select[@name='filterBy']", # User provided specific name
                "//select[@id='filterBy']",
                "//select[contains(@id, 'filter')]",
                "//select[contains(@name, 'filter')]",
                "//select[contains(@class, 'filter')]",
                "//select[contains(@onchange, 'filter')]",
                "//label[contains(text(), 'Filter')]/following::select",
            ]
            
            filter_dropdown = None
            for selector in filter_selectors:
                try:
                    filter_dropdown = WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                    logging.info(f"Found filter dropdown with selector: {selector}")
                    break
                except:
                    continue
            
            if not filter_dropdown:
                logging.error("Could not find filter dropdown")
                return True
            
            # Updated filter options
            select = Select(filter_dropdown)
            
            # Define target options based on input
            # User wants "Posted Last 3 Days" or "Posted Today"
            
            target_options = [filter_name]
            
            # Add reasonable variations just in case
            if "Today" in filter_name:
                target_options.extend(['Today', 'New Today', 'Posted Today'])
            elif "3 Days" in filter_name:
                target_options.extend(['Last 3 Days', 'Posted Last 3 Days'])
            
            option_selected = False
            for option_text in target_options:
                try:
                    select.select_by_visible_text(option_text)
                    logging.info(f"Selected filter option: {option_text}")
                    option_selected = True
                    break
                except:
                    continue
            
            # If visible text doesn't work, try fuzzy matching
            if not option_selected:
                try:
                    for option in select.options:
                        option_lower = option.text.lower()
                        # Simple fuzzy match based on key words
                        if filter_name.lower() in option_lower:
                             select.select_by_visible_text(option.text)
                             logging.info(f"Selected by fuzzy text: {option.text}")
                             option_selected = True
                             break
                except Exception as e:
                    logging.warning(f"Could not select filter option fuzzy: {e}")
            
            if not option_selected:
                 logging.warning(f"⚠️ Could not find '{filter_name}' filter option.")
                 # Print available options for debugging
                 try:
                     options_text = [o.text for o in select.options]
                     logging.info(f"Available options: {options_text}")
                 except:
                     pass
            
            # Wait for page to update
            time.sleep(3)

            
            return True
            
        except Exception as e:
            logging.error(f"Filter error: {e}")
            return True  # Continue anyway
            
        except Exception as e:
            logging.error(f"Filter error: {e}")
            return True  # Continue anyway
    
    # -- page controls: Show, Filter By, pagination ---------------------------
    #
    # The three controls on the opportunities listing:
    #
    #   <select id="allOppPageSize" name="pageSize">   25 | 50 | 75 | 100
    #   <select id="allOppFilterId" name="filterId">   -1 Select Criteria …
    #   <ul class="page-links"> … <a title="Next Page" href="…pageNum=2&…">
    #
    # Both selects reload the listing on change, and the Next link carries the
    # whole state (pageNum, pageSize, filterId) in its href — which is what the
    # pagination walk follows.

    PAGE_SIZE_SELECT = "allOppPageSize"
    FILTER_SELECT = "allOppFilterId"
    PAGE_SUMMARY = "span.page-summary"

    # How to find "go to the next page", most specific first.
    #
    # `title="Next Page"` alone was the whole of it, and it is the reason a
    # listing of 115 buys came back with 100: the control renders inside the
    # results table as a row of links reading "< Prev  1 2  Next >" (the row
    # `extract_request_data` has always had to skip), and on that markup the
    # anchor carries no title. The lookup found nothing, `collect_listing` read
    # that as "last page", and page 2 was never visited — silently, because the
    # summary line it would have checked itself against is in that same
    # unrecognised row.
    #
    # So: the titled anchor if the portal renders one, then the link by its
    # visible text, then any anchor whose href carries a pageNum. Each candidate
    # is checked for being *live* before it is followed — the last page renders
    # the same "Next >" text with the anchor disabled.
    NEXT_LINK_XPATHS = (
        "//a[@title='Next Page']",
        "//a[normalize-space(.)='Next >']",
        "//a[starts-with(normalize-space(.), 'Next')]",
        "//ul[contains(@class,'page-links')]//a[contains(@href,'pageNum=')][last()]",
    )

    # "1 - 100 of 115 Buys", wherever the portal chose to put it. Read from the
    # whole page rather than one element for the same reason as above: the line
    # is not always in `span.page-summary`.
    SUMMARY_RE = re.compile(
        r"(\d[\d,]*)\s*-\s*(\d[\d,]*)\s+of\s+(\d[\d,]*)\s+Buys", re.IGNORECASE
    )

    def set_page_size(self, size: str = "100") -> bool:
        """Set Show: to `size` results per page. Returns False if it isn't there.

        Fewer page loads for the same buys, and fewer chances for the listing to
        shift under the walk — the portal's own default is 25.
        """
        try:
            select = Select(WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, self.PAGE_SIZE_SELECT))
            ))
            if (select.first_selected_option.get_attribute("value") or "") == size:
                return True
            select.select_by_value(size)
            logging.info(f"Show: set to {size} per page")
            self._await_listing_reload()
            return True
        except Exception as exc:
            logging.warning(f"Could not set page size to {size}: {exc}")
            return False

    def apply_filter_id(self, filter_id: str) -> bool:
        """Select a Filter By: criterion by its option value ("3" = last 7 days).

        Selected by value rather than visible text: the values are stable ids,
        where the labels are display copy. "-1" is the portal's "Select
        Criteria" — no filter — and is left alone rather than selected, so an
        unfiltered run touches the control at all.
        """
        if not filter_id or str(filter_id) == "-1":
            logging.info("No Filter By criterion requested — reading the full listing")
            return True

        # Retried on a stale element, because the step before this one
        # (`set_page_size`) reloads the listing: the select found a moment ago is
        # detached by the time it is used, and the whole criterion was then
        # dropped on a warning — a run asking for "Posted Today" quietly read the
        # entire listing instead. Re-finding the control is all it needs.
        last_error: Exception | None = None
        for attempt in (1, 2, 3):
            try:
                select = Select(WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.ID, self.FILTER_SELECT))
                ))
                select.select_by_value(str(filter_id))
                # Selecting is the act; reading back the option's text is only
                # for the log line — and it is its own round-trip against a
                # control the selection has just caused the portal to re-render.
                # A live run failed exactly there: the criterion *was* applied,
                # the label read went stale, and the whole thing was reported as
                # "could not apply filter" over a logging detail.
                try:
                    label = select.first_selected_option.text.strip()
                except (StaleElementReferenceException, WebDriverException):
                    label = ""
                logging.info(
                    f"Filter By: {label or '(applied)'} (value {filter_id})"
                )
                self._await_listing_reload()
                return True
            except StaleElementReferenceException as exc:
                last_error = exc
                logging.info(
                    f"Filter By control went stale (attempt {attempt}/3) — re-finding it"
                )
                time.sleep(1)
            except Exception as exc:
                last_error = exc
                break

        # Loud, and not just a warning: the run is about to read a listing that
        # is not the one that was asked for, and every count downstream is
        # against the wrong denominator.
        logging.error(
            f"FILTER NOT APPLIED: could not select Filter By {filter_id} "
            f"({last_error}) — this run is reading the UNFILTERED listing"
        )
        return False

    def _await_listing_reload(self) -> None:
        """Wait for the listing to come back after a control changes it."""
        try:
            WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, self.PAGE_SUMMARY))
            )
        except TimeoutException:
            pass  # a listing with a single page may render no summary
        time.sleep(1.5)  # let the table finish swapping in

    def page_summary(self) -> str:
        """The "1 - 25 of 28 Buys" line, or "".

        Tries the dedicated element first and falls back to finding the line
        anywhere on the page, because on the markup that broke pagination it
        lives in a row of the results table rather than in `span.page-summary`.
        """
        try:
            text = self.driver.find_element(By.CSS_SELECTOR, self.PAGE_SUMMARY).text.strip()
            if text:
                return text
        except (NoSuchElementException, WebDriverException):
            pass
        try:
            body = self.driver.find_element(By.TAG_NAME, "body").text
        except (NoSuchElementException, WebDriverException):
            return ""
        match = self.SUMMARY_RE.search(body or "")
        return match.group(0) if match else ""

    def page_counts(self) -> tuple[int, int, int] | None:
        """`(first, last, total)` from the summary line, or None if it isn't there.

        The whole line, not just the total: `last` is what says whether this page
        is the end of the listing, which is the check that catches a Next link
        this code cannot see at all.
        """
        match = self.SUMMARY_RE.search(self.page_summary())
        if not match:
            return None
        return tuple(int(g.replace(",", "")) for g in match.groups())  # type: ignore[return-value]

    def expected_total(self) -> int | None:
        """How many buys the listing claims, from its summary line."""
        counts = self.page_counts()
        return counts[2] if counts else None

    def _is_live_link(self, link) -> bool:
        """Is this anchor one that actually goes somewhere?

        The last page renders the same "Next >" text with the link inert — no
        href, or a disabled class. Following that either reloads the page we are
        on (an infinite walk, caught later by the seen-URL guard) or throws.
        """
        try:
            if not (link.get_attribute("href") or "").strip():
                return False
            if (link.get_attribute("aria-disabled") or "").lower() == "true":
                return False
            classes = (link.get_attribute("class") or "").lower()
            return "disabled" not in classes
        except WebDriverException:
            return False

    def next_page_url(self) -> str | None:
        """The Next link's href, or None on the last page.

        Followed as a URL rather than clicked: the href already carries
        pageNum/pageSize/filterId, and a link that goes stale between being
        found and being clicked costs a whole page of buys.

        Each candidate selector is tried in turn (see NEXT_LINK_XPATHS) and the
        first *live* anchor wins. A "Next" that is present but inert is the last
        page, which is a different thing from no control at all — both end the
        walk, but only one of them would be a bug if it were wrong.
        """
        current = self._page_number(self._current_url())
        for xpath in self.NEXT_LINK_XPATHS:
            try:
                links = self.driver.find_elements(By.XPATH, xpath)
            except WebDriverException:
                continue
            for link in links:
                if not self._is_live_link(link):
                    continue
                href = link.get_attribute("href")
                target = self._page_number(href)
                # Forward only. The later candidates match on href shape rather
                # than on the word "Next", so without this they will happily
                # return the Prev link or the last numbered page — which on a
                # live run walked from the last page to a page 3 that does not
                # exist, extracted nothing from it, and reported the listing
                # short. A link whose page number cannot be read is allowed
                # through: it is the titled/"Next >" anchor doing its job on a
                # portal that paginates some other way.
                if current and target and target <= current:
                    continue
                return href
        return None

    def _current_url(self) -> str:
        try:
            return self.driver.current_url or ""
        except WebDriverException:
            return ""

    @staticmethod
    def _page_number(url: str | None) -> int | None:
        """The `pageNum` a listing URL points at, or None if it carries none."""
        if not url:
            return None
        values = parse_qs(urlparse(url).query).get("pageNum")
        if not values:
            return None
        try:
            return int(values[0])
        except (TypeError, ValueError):
            return None

    def page_url_for(self, page_number: int) -> str | None:
        """The listing URL for `page_number`, derived from where we are now.

        The arithmetic route to a page, used when the Next control cannot be
        found but the summary line proves there are more buys than have been
        read. The portal keeps its whole state in the query string
        (`pageNum`/`pageSize`/`filterId`), so a page is addressable directly.
        """
        try:
            current = self.driver.current_url or ""
        except WebDriverException:
            return None
        if not current:
            return None
        parsed = urlparse(current)
        params = parse_qs(parsed.query, keep_blank_values=True)
        if "pageNum" not in params and "pageSize" not in params:
            # Not a paged listing URL — guessing a query string for it would be
            # inventing a route the portal never showed us.
            return None
        params["pageNum"] = [str(page_number)]
        return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))

    def collect_listing(self, filter_id: str = "-1", page_size: str = "100",
                        max_pages: int = 100) -> list:
        """Every buy across every page of the listing, in portal order.

        Assumes an already-logged-in driver on the opportunities page. Walks
        Next until it runs out, guarding against a portal that keeps handing
        back the same page. `max_pages` is a runaway backstop, not a cap on
        results — a listing that hits it is logged loudly.
        """
        self.set_page_size(page_size)
        # Recorded, not just logged: a run that asked for "Posted Today" and read
        # the whole listing instead is reporting counts against the wrong
        # denominator, and the runner turns this into an error on the run.
        self.filter_applied = self.apply_filter_id(filter_id)

        rows: list = []
        seen_urls: set = set()
        seen_buys: set = set()
        # The total the listing claims, read on the *first* page. Read at the end
        # instead and it is the last page's summary — which agrees with itself
        # however few pages were walked, and so could never catch a walk that
        # stopped early. This number is what the walk is held to.
        counts = self.page_counts()
        expected = counts[2] if counts else None
        pages = 0

        # The counter stream. Every line carries the two numbers that matter —
        # what this page contributed and what the whole walk has against the
        # portal's own total — so a truncation shows up as it happens rather
        # than as a short spreadsheet hours later.
        logging.info(
            "[SEARCH EXECUTED]: Total %s Bids Detected across Pages.",
            expected if expected is not None else "an unstated number of",
        )

        while pages < max_pages:
            pages += 1
            # The rows this page claims, from the portal's own summary line
            # ("101 - 136 of 136 Buys"), falling back to arithmetic on the page
            # size when the line is not rendered.
            span = self.page_counts()
            if span:
                first, last = span[0], span[1]
            else:
                first = len(rows) + 1
                last = None
            logging.info(
                "[PAGE %d]: Extracting rows %d to %s...",
                pages, first, last if last is not None else "?",
            )
            page_rows = self.extract_request_data()
            # A row with no comparable key is kept, never matched: dropping a buy
            # is unrecoverable, a duplicate row is not.
            new_rows = [
                r for r in page_rows
                if not self._buy_key(r) or self._buy_key(r) not in seen_buys
            ]
            seen_buys.update(k for k in (self._buy_key(r) for r in new_rows) if k)
            rows.extend(new_rows)
            repeated = len(page_rows) - len(new_rows)
            wanted = (last - first + 1) if last is not None else len(page_rows)
            logging.info(
                " └── [PAGE %d %s]: %d/%d processed.%s Running total %d%s.",
                pages,
                "SUCCESS" if len(new_rows) >= wanted else "SHORT",
                len(new_rows), wanted,
                f" {repeated} row(s) already seen." if repeated else "",
                len(rows),
                f" of {expected}" if expected else "",
            )

            if expected is not None and len(rows) >= expected:
                break  # the listing is accounted for; nothing left to visit

            next_url = self.next_page_url()
            if not next_url or next_url in seen_urls:
                # No usable Next control. If the summary says buys remain, the
                # control is there and we cannot see it — go to the next page by
                # its number rather than reporting a short listing as complete.
                # This is what turned "115 detected, 100 read" into a silent
                # success: the walk believed page 1 was the last page.
                if expected is None or len(rows) >= expected:
                    break
                numbered = self.page_url_for(pages + 1)
                if not numbered or numbered in seen_urls:
                    logging.error(
                        f"{len(rows)} of {expected} buys read and no way forward from "
                        f"page {pages} — the pagination control was not recognised"
                    )
                    break
                logging.warning(
                    f"No Next link on page {pages} but {expected - len(rows)} buy(s) "
                    f"remain — continuing by page number"
                )
                next_url = numbered

            if not page_rows and pages > 1:
                # An empty page past the first means the walk has run off the end
                # of the listing; going on would only load more of them.
                logging.warning(f"Page {pages} held no buys — stopping the walk")
                break

            logging.info("[PAGINATING]: Navigating to Page %d...", pages + 1)
            seen_urls.add(next_url)
            self.driver.get(next_url)
            self._await_listing_reload()

        if pages >= max_pages:
            logging.error(f"Stopped after {max_pages} pages — pagination did not terminate")

        if expected is not None and len(rows) != expected:
            logging.error(
                f"INCOMPLETE LISTING: the portal reports {expected} buys and "
                f"{len(rows)} were read across {pages} page(s) — "
                f"{expected - len(rows)} not captured "
                f"({len(rows) / expected:.0%} coverage)"
            )
        else:
            logging.info(
                f"[LISTING COMPLETE]: Total Extracted: {len(rows)}"
                + (f" / Total Detected: {expected} (100% Coverage)" if expected
                   else " (the portal stated no total)")
                + f" across {pages} page(s)"
            )

        self.pages_scraped = pages
        self.expected_buys = expected
        return rows

    #: The keys `extract_request_data` actually builds its rows with. Named here
    #: rather than guessed at the call site: reading `buyer_number` (the *hub's*
    #: field name, applied later by the runner's `_listing_records`) made every
    #: row's key the empty string, so page 1's blank key matched page 2's and a
    #: whole page was discarded as "already seen".
    BUY_KEY_FIELDS = ("Buyer#", "Detail URL")

    @classmethod
    def _buy_key(cls, row: dict) -> str:
        """What makes two listing rows the same buy, or "" when nothing does.

        Rows are de-duplicated across pages because a listing that shifts under
        the walk (a buy closing while it runs) re-renders the same buy on the
        next page — counted twice, that hides a row that was genuinely missed.

        An empty key means *this row cannot be compared*, and the caller keeps
        it. Treating unkeyed rows as equal to each other is what turned a
        de-duplication guard into a page-shredder.
        """
        for field in cls.BUY_KEY_FIELDS:
            value = str(row.get(field) or "").strip().lower()
            if value:
                return value
        return ""

    def contains_excluded_keywords(self, description: str) -> bool:
        """Check if description contains any excluded keywords"""
        if not description:
            return False
        
        description_lower = description.lower()
        for keyword in self.keywords_to_exclude:
            if keyword in description_lower:
                logging.info(f"Excluded request containing keyword: {keyword}")
                return True
        return False
    
    def extract_request_data(self):
        """Extract data from request table with column mapping"""
        requests_data = []
        
        try:
            logging.info("Starting structured data extraction...")

            
            # Find the main table
            tables = self.driver.find_elements(By.TAG_NAME, "table")
            target_table = None
            
            # Logic to find the correct data table - look for one with many rows
            for table in tables:
                rows = table.find_elements(By.TAG_NAME, "tr")
                if len(rows) > 3:  # Arbitrary threshold
                    target_table = table
                    break
            
            if not target_table and tables:
                target_table = tables[0] # Fallback to first table
            
            if not target_table:
                logging.error("No data table found on the page.")
                return []
                
            # Map columns
            # Default indices (0-based) based on standard layout
            col_map = {
                'buy_number': 0,
                'description': 1,
                'buyer': -1,
                'end_date': -1
            }
            
            # Try to read headers
            headers = target_table.find_elements(By.TAG_NAME, "th")
            if not headers:
                # Some tables use first tr as header
                rows = target_table.find_elements(By.TAG_NAME, "tr")
                if rows:
                    headers = rows[0].find_elements(By.TAG_NAME, "td")
            
            # Dynamic Column Mapping
            if headers:
                logging.info(f"Found {len(headers)} columns. Mapping headers...")
                
                # Reset map to populate from headers
                col_map = {'buy_number': -1, 'description': -1, 'buyer': -1, 'end_date': -1}
                
                for i, header in enumerate(headers):
                    header_text = header.text.lower().strip()
                    logging.info(f"Column {i}: {header_text}")
                    
                    # Buy Number Detection
                    # Match 'buy' (but not 'buyer' unless it looks like an ID column) or '#'
                    if col_map['buy_number'] == -1:
                        if 'buy #' in header_text or 'rfq' in header_text or 'solicitation' in header_text:
                            col_map['buy_number'] = i
                        elif ('#' in header_text or 'buy' in header_text) and i < 2:
                             col_map['buy_number'] = i
                             
                    # Description Detection
                    if col_map['description'] == -1:
                        # Ensure we don't pick the same column as Buy Number (e.g. "Solicitation Name" matches both)
                        if i != col_map['buy_number']:
                            if 'description' in header_text or 'title' in header_text or 'name' in header_text:
                                col_map['description'] = i
                            
                    # Buyer Detection
                    if col_map['buyer'] == -1:
                        if 'buyer' in header_text and 'description' not in header_text:
                            col_map['buyer'] = i
                            
                    # End Date Detection
                    if col_map['end_date'] == -1:
                        if 'end' in header_text or 'due' in header_text or 'close' in header_text:
                            col_map['end_date'] = i
                
                # Fallbacks if still -1
                if col_map['buy_number'] == -1: col_map['buy_number'] = 0
                if col_map['description'] == -1: col_map['description'] = 1
                if col_map['buyer'] == -1: col_map['buyer'] = 2
                if col_map['end_date'] == -1: 
                    # Default end date to 4, or last column if less than 4
                    col_map['end_date'] = 4 if len(headers) > 4 else len(headers) - 1

            else:
                logging.warning("No headers found using default hypothesis.")
                col_map['buy_number'] = 0
                col_map['description'] = 1
                col_map['buyer'] = 2
                col_map['end_date'] = 4
            
            logging.info(f"Using column map: {col_map}")
            
            # Extract Data
            rows = target_table.find_elements(By.TAG_NAME, "tr")
            # Skip header row if it exists
            start_row_index = 1 if headers else 0
            
            for i in range(start_row_index, len(rows)):
                try:
                    row = rows[i]
                    cells = row.find_elements(By.TAG_NAME, "td")
                    
                    if not cells or len(cells) < 3:
                        continue
                        
                    # Extract Buy Number
                    buy_idx = col_map.get('buy_number', 0)
                    detail_url = ""
                    if buy_idx < len(cells):
                        buy_text = cells[buy_idx].text.strip().split('\n')[0] # Take first line only
                        buyer_id = buy_text
                        # The Buy # is a link to the buy's detail page
                        # (/fbweb/buyDetails.do?buy_id=…). Captured here, while
                        # the row is in hand, so the detail pass can visit each
                        # buy by URL instead of re-finding a link that the
                        # listing may have re-rendered under it.
                        links = cells[buy_idx].find_elements(By.TAG_NAME, "a")
                        if links:
                            detail_url = links[0].get_attribute("href") or ""
                            # The anchor text is the authoritative Buy # — the
                            # cell's text can carry a status word underneath it.
                            link_text = (links[0].text or "").strip()
                            if link_text:
                                buyer_id = link_text
                    else:
                        buyer_id = ""

                    # Skip empty rows or single characters
                    if not buyer_id or len(buyer_id) < 2:
                        continue

                    # Skip pagination/bad rows
                    # The user provided images show rows with "< Prev", "Next >", "1 - 11 of 11 Buys"
                    low_id = buyer_id.lower()
                    if 'prev' in low_id or 'next' in low_id or 'buys' in low_id or 'page' in low_id:
                        logging.info(f"Skipping pagination row: {buyer_id}")
                        continue
                        
                    # Extract Description
                    desc_idx = col_map.get('description', 1)
                    if desc_idx > -1 and desc_idx < len(cells):
                        description = cells[desc_idx].text.strip()
                    else:
                        description = "No Description"

                    # Skip if description looks like pagination too (sometimes columns shift)
                    if 'prev' in description.lower() or 'next' in description.lower() or '1 -' in description:
                         continue

                    # Extract Buyer (Agency)
                    buyer_idx = col_map.get('buyer', 2)
                    if buyer_idx > -1 and buyer_idx < len(cells):
                         buyer_agency = cells[buyer_idx].text.strip()
                    else:
                         buyer_agency = "No Buyer"
                        
                    # Extract End Date
                    date_idx = col_map.get('end_date', -1)
                    end_date = "Not Found"
                    if date_idx > -1 and date_idx < len(cells):
                         end_date = cells[date_idx].text.strip()
                    else:
                        # Fallback: look for date pattern in all cells
                        for cell in cells:
                            txt = cell.text
                            # Simple date regex
                            match = re.search(r'\d{1,2}/\d{1,2}/\d{2,4}', txt)
                            if match:
                                end_date = match.group(0)
                                break
                    
                    # Logic Checks
                    if buyer_id in self.processed_ids:
                        continue
                        
                    if self.contains_excluded_keywords(description):
                        continue
                        
                    # Construct Record
                    request_data = {
                        'Buyer#': buyer_id,
                        'Buyer Description': description[:500].replace('\n', ' '), # Clean newlines
                        'Buyer': buyer_agency,
                        'End Date': end_date,
                        'Detail URL': detail_url,
                    }
                    
                    requests_data.append(request_data)
                    self.processed_ids.add(buyer_id)
                    logging.info(f"Extracted: {buyer_id} | {end_date}")
                    
                except Exception as row_error:
                    logging.warning(f"Error processing row {i}: {row_error}")
                    continue
            
            logging.info(f"Successfully extracted {len(requests_data)} requests")
            
        except Exception as e:
            logging.error(f"Extraction error: {e}")

        
        return requests_data
    
    def save_to_csv(self, data: List[Dict]):
        """Save extracted data to CSV file"""
        if not data:
            logging.info("No new data to save")
            return
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.csv_file) if os.path.dirname(self.csv_file) else '.', exist_ok=True)
        
        file_exists = os.path.exists(self.csv_file)
        
        try:
            with open(self.csv_file, 'a', newline='', encoding='utf-8') as file:
                fieldnames = ['Buyer#', 'Buyer Description', 'Buyer', 'End Date']
                writer = csv.DictWriter(file, fieldnames=fieldnames)
                
                if not file_exists:
                    writer.writeheader()
                
                for row in data:
                    writer.writerow(row)
                
            logging.info(f"Saved {len(data)} new records to {self.csv_file}")
            
        except Exception as e:
            logging.error(f"Error saving to CSV: {e}")
    
    def open_listing(self, filter_id: str = "-1", page_size: str = "100") -> list:
        """Log in, reach the opportunities listing, and read every page of it.

        The half of a run that needs a browser and a session. The driver is left
        open and signed in so the caller can go on to the detail pages; closing
        it is the caller's job (`self.driver.quit()`).
        """
        self.setup_driver()
        self.load_existing_data()
        if not self.login():
            raise RuntimeError("Unison login failed — check the credentials in server/.env")
        self.navigate_to_requests_page()
        return self.collect_listing(filter_id=filter_id, page_size=page_size)

    def run_scraper(self, filter_by=None):
        """Main method to run the entire scraping process.

        The standalone path: listing -> CSV -> close. The hub does not use it —
        its runner calls `open_listing` and keeps the session for the detail
        pass — so this stays as the way to drive the engine on its own.

        `filter_by` names an option in the dashboard's filter dropdown. Passing
        None means *do not touch the dropdown*: the run reads the default
        listing. It used to fall back to "Posted Today", which quietly limited
        every run to same-day requests.
        """
        logging.info("=" * 60)
        logging.info("STARTING UNISON MARKETPLACE SCRAPER")
        logging.info("=" * 60)

        filter_name = filter_by

        try:
            # Step 1: Setup
            print("\nStep 1: Setting up browser...")
            self.setup_driver()
            self.load_existing_data()
            
            # Step 2: Login
            print("Step 2: Logging in...")
            if not self.login():
                print("❌ Login failed. Check credentials and .env file.")

                self.driver.quit()
                return
            
            # Step 3: Navigate to requests
            print("Step 3: Navigating to requests page...")
            self.navigate_to_requests_page()
            
            # Step 4: Apply filter (skipped when none was asked for)
            if filter_name:
                print(f"Step 4: Applying '{filter_name}' filter...")
                self.apply_filter(filter_name)
            else:
                print("Step 4: No filter requested — reading the default listing.")
                logging.info("No dashboard filter applied; taking the default listing")

            # Step 5: Extract data — 100 per page, every page.
            print("Step 5: Extracting request data...")
            self.set_page_size("100")
            all_data = []
            seen_pages = set()
            while True:
                all_data.extend(self.extract_request_data())
                next_url = self.next_page_url()
                if not next_url or next_url in seen_pages:
                    break
                seen_pages.add(next_url)
                self.driver.get(next_url)
                self._await_listing_reload()

            # Step 6: Save data
            print("Step 6: Saving data...")
            self.save_to_csv(all_data)
            
            # Summary
            print("\n" + "=" * 60)
            print("SCRAPING COMPLETE")
            print("=" * 60)
            print(f"✓ Extracted: {len(all_data)} new requests")
            print(f"✓ Saved to: {self.csv_file}")
            print(f"✓ Log file: logs/unison_scraper.log")
            print("=" * 60)
            
            if len(all_data) == 0:
                print("\n⚠️  No data extracted. Possible reasons:")
                print("The dashboard listing is empty, or every row was already seen this run")
            
        except Exception as e:
            print(f"\n❌ ERROR: {e}")
            logging.error(f"Scraping failed: {e}")
            
        finally:
            if self.driver:
                # Keep browser open for 10 seconds if in debug mode
                if len(self.processed_ids) == 0 or os.getenv('DEBUG_MODE', 'false').lower() == 'true':
                    print("\nKeeping browser open for 10 seconds for debugging...")
                    print(f"Current URL: {self.driver.current_url}")
                    time.sleep(10)
                
                self.driver.quit()
                print("Browser closed.")

def main():
    """Main execution function"""
    print("=" * 60)
    print("UNISON MARKETPLACE SCRAPER")
    print("=" * 60)
    
    # Check for .env file
    env_file = '.env'
    if not os.path.exists(env_file):
        print("\n⚠️  ERROR: No .env file found!")
        print("Create a file named '.env' with your credentials:")
        print("-" * 40)
        print("UNISON_EMAIL=your_email@example.com")
        print("UNISON_PASSWORD=your_password")
        print("-" * 40)
        print("\nCreate this file in the same folder as the script.")
        return
    
    # Check if credentials are set
    load_dotenv()
    email = os.getenv('UNISON_EMAIL')
    password = os.getenv('UNISON_PASSWORD')
    
    if not email or email == 'your_email@example.com' or not password or password == 'your_password':
        print("\n⚠️  ERROR: Update your credentials in the .env file!")
        print("Current .env file:")
        with open(env_file, 'r') as f:
            print(f.read())
        return
    
    print(f"Using account: {email[:3]}...{email[email.find('@'):]}")
    
    # Run scraper
    scraper = UnisonMarketplaceScraper()
    scraper.run_scraper()

if __name__ == "__main__":
    main()