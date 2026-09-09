import re
import requests
import os
from pathlib import Path
from typing import TYPE_CHECKING
from playwright.sync_api import sync_playwright
from scraper_utils import download_image_cached, now_timestamp, write_json, log, offer_summary, skip_if_blacklisted

if TYPE_CHECKING:
    from playwright._impl._api_structures import SetCookieParam

# setup
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "cbb"
IMAGE_DIR = BASE_DIR / "public" / "images" / "cbb"
OUTPUT_PATH = DATA_DIR / "cbb_offers.json"


def download_image(image_url, product_name):
    return download_image_cached(
        image_url,
        product_name,
        IMAGE_DIR,
        "/images/cbb",
        base_url="https://www.cbb.dk",
    )

def parse_price(text):
    # extract integer price from a string like '1.064 kr.' or '39 kr./md.
    if not text:
        return None
    cleaned = re.sub(r'\D', '', text.replace('.', ''))
    try:
        return int(cleaned)
    except ValueError:
        return None


def get_min_cost_from_page(page, url):
    # minimum 6 month price is more complicated to extract because of the way CBB structures their offers with a mix of upfront price and subscription options
    # returns int or None
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1500)

        # kontant pris / upfront price
        kontant_price = None

        # look for a "Mindstepris inkl. X mdr. abonnement" line - take the LAST number
        mindste_texts = page.locator('text=/Mindstepris/').all_text_contents()
        for raw in mindste_texts:
            raw = raw.replace('\xa0', ' ')
            matches = re.findall(r'(\d{1,3}(?:\.\d{3})+|\d{3,})', raw)
            if matches:
                return int(matches[-1].replace('.', ''))

        # fallback : "Kontant" price block
        for selector in ['text=Kontant', 'text=Betal kontant', 'text=Betales kontant']:
            kontant_block = page.locator(selector).first
            if kontant_block.count():
                try:
                    parent = kontant_block.locator('xpath=ancestor::*[self::div or self::li or self::button][1]')
                    price_text = parent.locator('span, strong, p').first.text_content()
                    kontant_price = parse_price(price_text)
                    if kontant_price:
                        break
                except Exception:
                    pass

        if not kontant_price:
            # fallback: "Betales nu" total
            try:
                betales_nu_el = page.locator('text=Betales nu').locator('xpath=following-sibling::*[1]').first
                if betales_nu_el.count():
                    total = parse_price(betales_nu_el.text_content())
                    fragt_el = page.locator('text=Fragt').locator('xpath=following-sibling::*[1]').first
                    fragt = parse_price(fragt_el.text_content()) if fragt_el.count() else 65
                    if total:
                        kontant_price = total - (fragt or 65)
            except Exception:
                pass

        if not kontant_price:
            # fallback: largest standalone price-looking number on the page
            price_els = page.locator('text=/^\\d{1,2}\\.\\d{3}\\s*kr\\.?$/').all_text_contents()
            candidates = []
            for t in price_els:
                v = parse_price(t)
                if v and v > 500:
                    candidates.append(v)
            if candidates:
                kontant_price = min(candidates)  # cheapest upfront price

        # --- Monthly subscription price ---
        monthly_price = None
        promo_price = None
        promo_months = None
        regular_price = None

        info_texts = page.locator('text=/kr\\.?\\/md/').all_text_contents()
        for info in info_texts:
            info = info.replace('\xa0', ' ')
            # Pattern: "39 kr./md. i 2 md. - Herefter 129 kr."
            m = re.search(
                r'([\d.]+)\s*kr\.?/md\.?\s+i\s+(\d+)\s+md\.?\s*[-–]\s*[Hh]erefter\s+([\d.]+)\s*kr',
                info
            )
            if m:
                promo_price = int(m.group(1).replace('.', ''))
                promo_months = int(m.group(2))
                regular_price = int(m.group(3).replace('.', ''))
                break

            # Simpler pattern: just "X kr./md."
            if not monthly_price:
                m2 = re.search(r'([\d.]+)\s*kr\.?/md', info)
                if m2:
                    monthly_price = int(m2.group(1).replace('.', ''))

        if kontant_price and promo_price is not None and promo_months is not None and regular_price is not None:
            remaining_months = max(0, 6 - promo_months)
            total = kontant_price + (promo_months * promo_price) + (remaining_months * regular_price)
            return total, promo_price, regular_price

        if kontant_price and monthly_price:
            return kontant_price + 6 * monthly_price, monthly_price, None

    except Exception as e:
        log(f"  Error scraping {url}: {e}")

    return None, None, None


def build_entry(phone, page, date_time):
    product_name = phone.get("headline", "Ukendt model")

    # format product link
    url_path = phone.get("url")
    product_link = f"https://www.cbb.dk{url_path}" if url_path else ""

    # get image
    raw_image_url = phone.get("image", {}).get("url")
    local_image_path = download_image(raw_image_url, product_name)

    # price
    price_with_subscription = phone.get("priceInt")

    # check stock status
    sold_out = "true" if phone.get("buttonText", "").upper() == "UDSOLGT" else "false"

    # get accurate min cost by visiting the product page
    min_cost = None
    monthly_price = None
    monthly_price_after_promo = None
    if product_link:
        min_cost, monthly_price, monthly_price_after_promo = get_min_cost_from_page(page, product_link)

    return {
        "link": product_link,
        "product_name": product_name,
        "image_url": local_image_path,
        "provider": "CBB",
        "type": "phone",
        "signup_price": 0,
        "data_gb": 0,
        "price_without_subscription": 0,
        "price_with_subscription": price_with_subscription,
        "subscription_price_monthly": monthly_price,
        "subscription_price_monthly_after_promo": monthly_price_after_promo,
        "min_cost_6_months": min_cost,
        "discount_on_product": 0,
        "saved_at": date_time,
        "sold_out": sold_out
    }


def scrape_cbb():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    date_time = now_timestamp()
    cleaned_results = []

    # cbb's direct api endpoint for loading phones
    api_url = "https://www.cbb.dk/api/product/load-phones/"
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    log("Fetching product list from CBB API...")
    try:
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        raw_data = response.json()
    except requests.RequestException as e:
        log(f"Couldn't fetch data from CBB API: {e}")
        return

    phones_list = raw_data.get("content", {}).get("phones", [])
    log(f"Found {len(phones_list)} products in JSON")

    is_ci = os.environ.get('CI') == 'true'

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=is_ci)
        context = browser.new_context(
            locale="da-DK",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        # accept cookies up front by injecting consent cookies
        consent_cookies: list["SetCookieParam"] = [
            {"name": "CookieInformationConsent", "value": "true", "domain": ".cbb.dk", "path": "/"},
        ]
        context.add_cookies(consent_cookies)
        page = context.new_page()

        for phone in phones_list:
            entry = build_entry(phone, page, date_time)
            product_name = str(entry.get("product_name", ""))
            if not skip_if_blacklisted(product_name):
                cleaned_results.append(entry)
                offer_summary(
                    product_name,
                    sub=entry["price_with_subscription"],
                    rabat=entry["discount_on_product"],
                    kontant=entry["price_without_subscription"],
                    min6=entry["min_cost_6_months"],
                    md=entry["subscription_price_monthly"],
                )
            else:
                log(f"  Skipping used product: {product_name}")

        browser.close()

    # save output
    write_json(OUTPUT_PATH, cleaned_results)

    log(f"\nScraping complete. Saved {len(cleaned_results)} offers to 'cbb_offers.json'")


if __name__ == "__main__":
    scrape_cbb()