import os
import asyncio
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv(override=True)

BIDNET_DIRECT_LINK = os.getenv("BIDNET_DIRECT_LINK")
USERNAME = os.getenv("USERNAME")
PASSWORD = os.getenv("PASSWORD")

async def test_scrape():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel="chrome")
        context = await browser.new_context()
        page = await context.new_page()
        
        print("Navigating...")
        await page.goto(BIDNET_DIRECT_LINK)
        await page.click("#header_btnLogin")
            
        await page.wait_for_selector("#j_username")
        await page.fill("#j_username", USERNAME)
        await page.fill("#j_password", PASSWORD)
        await page.click("#loginButton")
        print("Logged in, waiting for dashboard...")
        await page.wait_for_selector("#btnSolicitations", timeout=15000)
        
        print("Searching...")
        await page.wait_for_selector("#solicitationSingleBoxSearch")
        await page.fill("#solicitationSingleBoxSearch", "graphic design")
        await page.click("#topSearchButton")
        
        await page.wait_for_timeout(3000)
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except:
            pass
        
        await page.wait_for_selector(".searchContentGroupContainer", state="visible")
        print("Filtering...")
        await page.click("div[search-content-group-id='2085061601']")
        
        await page.wait_for_timeout(4000)
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except:
            pass
            
        await page.wait_for_selector("table tbody tr.mets-table-row")
        
        row_locators = await page.locator("tr.mets-table-row a.solicitationsTitleLink").all()
        links = []
        for row in row_locators:
            href = await row.get_attribute("href")
            if href:
                links.append("https://www.bidnetdirect.com" + href if href.startswith("/") else href)

        print(f"Found {len(links)} solicitations")
        
        if not links:
            return
            
        # Try the first link
        details_page = await context.new_page()
        await details_page.goto(links[0])
        await details_page.wait_for_load_state("networkidle")
        
        print("Clicking Documents tab...")
        try:
            await details_page.click("a[title='Documents & Items']")
            await details_page.wait_for_timeout(3000)
            
            # Extract document names
            doc_rows = await details_page.locator("table tbody tr.mets-table-row").all()
            print("Found doc rows:", len(doc_rows))
            
            for doc in doc_rows:
                text = await doc.inner_text()
                print("Doc:", text)
                
        except Exception as e:
            print("Error clicking docs tab:", e)
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_scrape())
