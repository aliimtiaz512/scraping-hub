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
        browser = await p.chromium.launch(headless=True, channel="chrome", args=['--no-sandbox'])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()
        
        print("Navigating...")
        await page.goto(BIDNET_DIRECT_LINK, wait_until="networkidle")
        try:
            await page.click("#header_btnLogin", timeout=10000)
        except Exception as e:
            print("Failed header_btnLogin:", e)
            await page.screenshot(path="debug_investigate.png")
            return
            
        await page.wait_for_selector("#j_username")
        await page.fill("#j_username", USERNAME)
        await page.fill("#j_password", PASSWORD)
        await page.click("#loginButton")
        print("Logged in...")
        await page.wait_for_selector("#btnSolicitations", timeout=15000)
        
        print("Searching...")
        await page.fill("#solicitationSingleBoxSearch", "graphic design")
        await page.click("#topSearchButton")
        
        await page.wait_for_selector(".searchContentGroupContainer", state="visible")
        print("Filtering...")
        await page.click("div[search-content-group-id='2085061601']")
        
        await page.wait_for_timeout(4000)
        await page.wait_for_selector("table tbody tr.mets-table-row")
        
        row_locators = await page.locator("tr.mets-table-row a.solicitationsTitleLink").all()
        if not row_locators:
            return
            
        href = await row_locators[0].get_attribute("href")
        link = "https://www.bidnetdirect.com" + href if href.startswith("/") else href
        
        details_page = await context.new_page()
        await details_page.goto(link)
        await details_page.wait_for_load_state("networkidle")
        
        print("Clicking Documents tab...")
        try:
            await details_page.click("a[title='Documents & Items']")
            await details_page.wait_for_timeout(3000)
            html = await details_page.content()
            with open("docs_html.txt", "w") as f:
                f.write(html)
            print("Saved docs_html.txt")
        except Exception as e:
            print("Error clicking docs tab:", e)
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_scrape())
