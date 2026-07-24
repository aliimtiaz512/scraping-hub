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
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
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
        # Create new CSV file with timestamp for each run
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.csv_file = f'unison_requests_{timestamp}.csv'
        self.processed_ids = set()
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
        
        # Remove automation flags to avoid detection
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        
        self.driver = webdriver.Chrome(options=options)
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        logging.info("WebDriver initialized successfully")
        
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
                    if buy_idx < len(cells):
                        buy_text = cells[buy_idx].text.strip().split('\n')[0] # Take first line only
                        buyer_id = buy_text
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
                        'End Date': end_date
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
    
    def run_scraper(self, filter_by=None):
        """Main method to run the entire scraping process"""
        logging.info("=" * 60)
        logging.info("STARTING UNISON MARKETPLACE SCRAPER")
        logging.info("=" * 60)
        
        # Determine filter name
        filter_name = filter_by if filter_by else "Posted Today"
        
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
            
            # Step 4: Apply filter
            print(f"Step 4: Applying '{filter_name}' filter...")
            self.apply_filter(filter_name)
            
            # Step 5: Extract data
            print("Step 5: Extracting request data...")
            all_data = self.extract_request_data()
            
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
                print("No requests posted today")
            
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