from playwright.sync_api import sync_playwright
import pricerunner_scraper

PRODUCT = "Samsung Galaxy Tab S11 128GB"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    try:
        context, page = pricerunner_scraper.make_fresh_page(browser)
        price, ok = pricerunner_scraper.get_market_price(page, PRODUCT)
        print(f"Lookup for '{PRODUCT}': price={price}, ok={ok}")
    finally:
        try:
            context.close()
        except Exception:
            pass
        browser.close()

