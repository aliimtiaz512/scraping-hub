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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        await page.goto(BIDNET_DIRECT_LINK)
        await page.click("#header_btnLogin")
        await page.wait_for_selector("#j_username")
        await page.fill("#j_username", USERNAME)
        await page.fill("#j_password", PASSWORD)
        await page.click("#loginButton")
        
        # Go directly to the bid details page for 0000422262
        await page.goto("https://www.bidnetdirect.com/private/supplier/interception/open-solicitation/8825387655?target=view")
        await page.wait_for_load_state("domcontentloaded")
        
        docs_tab = page.locator("#docs-itemsAbstractTab a").first
        await docs_tab.wait_for(state="visible", timeout=15000)
        await docs_tab.click(timeout=10000)
        await page.wait_for_timeout(4000)
        
        buttons = await page.locator("table tbody tr a[title*='Download'], table tbody tr a[title*='download'], table tbody tr a[href*='download'], table tbody tr a:has-text('Download')").all()
        for i, btn in enumerate(buttons):
            html = await btn.evaluate("el => el.outerHTML")
            print(f"Button {i}: {html}")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_scrape())
