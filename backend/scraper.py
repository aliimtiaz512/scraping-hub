import os
import asyncio
from playwright.async_api import async_playwright
from dotenv import load_dotenv

# Default values, but will dynamically reload them in the function if needed

async def extract_field(page, field_name, timeout=5000):
    try:
        # Locate the container that has the field label, then extract the p tag inside mets-field-body
        locator = page.locator(f".mets-field:has-text('{field_name}') .mets-field-body p").first
        return await locator.inner_text(timeout=timeout)
    except Exception as e:
        print(f"Failed to extract {field_name}: {e}")
        return ""

async def scrape_bids(keyword: str):
    load_dotenv(override=True)
    bidnet_link = os.getenv("BIDNET_DIRECT_LINK")
    username = os.getenv("USERNAME")
    password = os.getenv("PASSWORD")
    base_document_folder = os.getenv("DOCUMENT_FOLDER", "./Documents")
    # Build a per-run folder named after the keyword used for this run, e.g. Documents_graphic_design
    safe_keyword = "".join([c if (c.isalnum() or c in (" ", "_", "-")) else "" for c in keyword]).strip().replace(" ", "_")
    if not safe_keyword:
        safe_keyword = "bids"
    document_folder = f"{base_document_folder}_{safe_keyword}"
    os.makedirs(document_folder, exist_ok=True)
    print(f"Documents for this run will be saved in: {document_folder}")

    async with async_playwright() as p:
        # Running in headless=False so you can see what is happening!
        browser = await p.chromium.launch(headless=False, channel="chrome")
        context = await browser.new_context()
        page = await context.new_page()
        
        scraped_data = []

        try:
            # 1. Navigate to homepage
            await page.goto(bidnet_link)
            
            # Click the main header login button to go to the login page
            await page.click("#header_btnLogin")
            await page.wait_for_selector("#j_username")
            
            # Enter credentials using correct element IDs from the BidNet login page
            await page.fill("#j_username", username)
            await page.fill("#j_password", password)
            
            # Click login button
            await page.click("#loginButton")
            
            # Wait for either the dashboard to load or an error message to appear
            try:
                # The prompt shows #btnSolicitations is available after login
                await page.wait_for_selector("#btnSolicitations", timeout=15000)
            except Exception as e:
                print("Login failed or took too long. Check credentials or CAPTCHA.")
                await page.screenshot(path="login_error.png")
                raise e

            # Note: The dashboard already contains the search bar, so we don't need to click the sidebar links.
            
            # 3. Enter keyword and search
            # Wait for the search textarea to appear
            await page.wait_for_selector("#solicitationSingleBoxSearch")
            await page.fill("#solicitationSingleBoxSearch", keyword)
            await page.click("#topSearchButton")
            
            # Wait for the search AJAX request to complete
            await page.wait_for_timeout(3000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except:
                pass
            
            # Wait for results to update
            await page.wait_for_selector(".searchContentGroupContainer", state="visible")

            # 4. Click Member Agency Bids
            await page.click("div[search-content-group-id='2085061601']")
            
            # Wait for the filter AJAX request to complete before extracting rows
            await page.wait_for_timeout(4000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except:
                pass
                
            await page.wait_for_selector("table tbody tr.mets-table-row")
            
            # 5. Extract links from table across ALL result pages (handle pagination)
            links = []
            page_num = 1
            max_pages = 100  # safety guard to avoid any accidental infinite loop
            while True:
                # Collect links on the current results page (dedup to be safe)
                row_locators = await page.locator("tr.mets-table-row a.solicitationsTitleLink").all()
                for row in row_locators:
                    href = await row.get_attribute("href")
                    if href:
                        full_link = "https://www.bidnetdirect.com" + href if href.startswith("/") else href
                        if full_link not in links:
                            links.append(full_link)

                print(f"Collected links from results page {page_num} (total so far: {len(links)})")

                if page_num >= max_pages:
                    print("Reached max page safety limit. Stopping pagination.")
                    break

                # Remember the current first-row href so we can detect when the page actually changes
                try:
                    first_href_before = await page.locator("tr.mets-table-row a.solicitationsTitleLink").first.get_attribute("href")
                except:
                    first_href_before = None

                # Try to find an enabled "next page" control.
                # On BidNet the next link is <a rel="next" class="next mets-pagination-page-icon">.
                # When there is no next page it renders as a disabled <span>, so an <a> match
                # is itself the signal that another page exists.
                next_button = None
                next_selectors = [
                    "a.next.mets-pagination-page-icon:not(.disabled)",
                    "a[rel='next']:not(.disabled)",
                    "a.next:not(.disabled)",
                ]
                for sel in next_selectors:
                    candidate = page.locator(sel).first
                    try:
                        if await candidate.count() > 0 and await candidate.is_visible():
                            next_button = candidate
                            break
                    except:
                        continue

                if next_button is None:
                    print("No further pages found. Pagination complete.")
                    break

                try:
                    await next_button.click(timeout=5000)
                except Exception as e:
                    print(f"Could not click next page: {e}")
                    break

                # Wait for the results table to refresh after navigating to the next page
                await page.wait_for_timeout(3000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except:
                    pass
                try:
                    await page.wait_for_selector("table tbody tr.mets-table-row", timeout=10000)
                except:
                    pass

                # Confirm the page actually advanced; if results are unchanged, stop
                try:
                    first_href_after = await page.locator("tr.mets-table-row a.solicitationsTitleLink").first.get_attribute("href")
                except:
                    first_href_after = None
                if first_href_after == first_href_before:
                    print("Next page did not change results. Stopping pagination.")
                    break

                page_num += 1

            print(f"Found {len(links)} solicitations for keyword '{keyword}'")

            # 6. Process each bid details page
            for link in links:
                try:
                    details_page = await context.new_page()
                    await details_page.goto(link, wait_until="domcontentloaded", timeout=60000)
                    try:
                        await details_page.wait_for_load_state("networkidle", timeout=10000)
                    except:
                        pass

                    reference_number = await extract_field(details_page, "Reference Number")
                    solicitation_number = await extract_field(details_page, "Solicitation Number")
                    solicitation_type = await extract_field(details_page, "Solicitation Type")
                    title = await extract_field(details_page, "Title")
                    publication_date = await extract_field(details_page, "Publication")
                    question_acceptance_deadline = await extract_field(details_page, "Question Acceptance Deadline")
                    closing_date = await extract_field(details_page, "Closing Date")
                    
                    # Documents count
                    try:
                        docs_tab = details_page.locator("#docs-itemsAbstractTab a").first
                        await docs_tab.wait_for(state="visible", timeout=15000)
                        docs_locator = docs_tab.locator(".tabCount").first
                        documents_count = await docs_locator.inner_text(timeout=5000)
                        if not documents_count.strip():
                            documents_count = "0"
                    except Exception as e:
                        print(f"Error getting doc count for {reference_number}: {e}")
                        documents_count = "0"

                    # Download documents if present
                    if documents_count != "0":
                        try:
                            await docs_tab.click(timeout=10000)
                            await details_page.wait_for_timeout(4000)
                            
                            safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c == ' ']).rstrip()
                            if not safe_title:
                                safe_title = "Bid"
                            bid_folder = os.path.join(document_folder, f"{reference_number} - {safe_title}")
                            os.makedirs(bid_folder, exist_ok=True)
                            
                            # Target only the download links inside the table rows to avoid bulk download buttons
                            download_buttons = await details_page.locator("table tbody tr a[title*='Download'], table tbody tr a[title*='download'], table tbody tr a[href*='download'], table tbody tr a:has-text('Download')").all()
                            print(f"Found {len(download_buttons)} download buttons for {reference_number}")
                            
                            for i, btn in enumerate(download_buttons):
                                try:
                                    async with details_page.expect_download(timeout=15000) as download_info:
                                        await btn.click(timeout=5000, force=True)
                                    download = await download_info.value
                                    file_path = os.path.join(bid_folder, download.suggested_filename)
                                    await download.save_as(file_path)
                                    print(f"Downloaded: {download.suggested_filename} to {file_path}")
                                except Exception as inner_e:
                                    # Fallback: try to fetch the href directly if expect_download fails
                                    try:
                                        href = await btn.get_attribute("href")
                                        if href and not href.startswith("javascript"):
                                            if href.startswith("/"):
                                                href = "https://www.bidnetdirect.com" + href
                                            print(f"Fallback downloading {href} for document {i}...")
                                            resp = await context.request.get(href, timeout=60000)
                                            if resp.ok:
                                                body = await resp.body()
                                                # Try to get filename from content-disposition or button text
                                                cd = resp.headers.get("content-disposition", "")
                                                filename = f"document_{i}.pdf"
                                                if "filename=" in cd:
                                                    # Extract filename carefully, handling quotes
                                                    filename_part = cd.split("filename=")[-1]
                                                    filename = filename_part.split(";")[0].strip('"').strip("'")
                                                else:
                                                    text = await btn.inner_text()
                                                    if text:
                                                        filename = text.strip()
                                                
                                                file_path = os.path.join(bid_folder, filename)
                                                with open(file_path, "wb") as f:
                                                    f.write(body)
                                                print(f"Fallback Downloaded: {filename} to {file_path}")
                                            else:
                                                raise Exception(f"Fallback failed with status {resp.status}")
                                        else:
                                            raise Exception("No valid href for fallback")
                                    except Exception as fallback_e:
                                        print(f"Skipped document {i} for {reference_number} (Timeout or requires acknowledgement popup). Fallback error: {fallback_e}")
                                        try:
                                            # Press escape in case a 'Terms' or 'Acknowledge Addendum' modal was opened
                                            await details_page.keyboard.press("Escape")
                                        except:
                                            pass
                        except Exception as e:
                            print(f"Error accessing documents tab for {reference_number}: {e}")

                    scraped_data.append({
                        "reference_number": reference_number.strip(),
                        "solicitation_number": solicitation_number.strip(),
                        "solicitation_type": solicitation_type.strip(),
                        "title": title.strip(),
                        "publication_date": publication_date.strip(),
                        "question_acceptance_deadline": question_acceptance_deadline.strip(),
                        "closing_date": closing_date.strip(),
                        "documents_count": documents_count.strip()
                    })

                except Exception as e:
                    print(f"Error processing bid {link}: {e}")
                finally:
                    try:
                        await details_page.close()
                    except:
                        pass

        except Exception as e:
            print(f"Scraping error: {e}")
            await page.screenshot(path="error.png")
            print("Saved screenshot of the error state to error.png")
        finally:
            await browser.close()
            
        return scraped_data

if __name__ == "__main__":
    # Test script execution
    res = asyncio.run(scrape_bids("AI"))
    print(res)
