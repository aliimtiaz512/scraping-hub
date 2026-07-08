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
        
        await page.goto("https://www.bidnetdirect.com/private/solicitations/8825387655/abstract/docs-items/8827451145/attachment-download")
        await page.wait_for_timeout(3000)
        
        # Test using context.request.get
        url = "https://www.bidnetdirect.com/private/solicitations/8825387655/abstract/docs-items/8827451145/attachment-download"
        print(f"Fetching {url}")
        resp = await context.request.get(url)
        print(f"Status: {resp.status}")
        headers = resp.headers
        print(f"Headers: {headers}")
        body = await resp.body()
        print(f"Body size: {len(body)} bytes")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_scrape())
