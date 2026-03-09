import json
import datetime
import re
import requests
import os
from playwright.sync_api import sync_playwright

# setup
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def download_image(image_url, product_name):
    if not image_url or not product_name:
        return ""

    # cbb uses relative image paths, so we prepend the domain
    if image_url.startswith('/'):
        image_url = f"https://www.cbb.dk{image_url}"

    # clean product name to create a safe filename
    filename = re.sub(r'[^a-z0-9]', '_', product_name.lower()) + ".webp"
    save_path = os.path.join(BASE_DIR, f"public/images/cbb/{filename}")
    os.makedirs(os.path.join(BASE_DIR, "public/images/cbb"), exist_ok=True)

    if os.path.exists(save_path):
        return f"/images/cbb/{filename}"

    try:
        response = requests.get(image_url)
        if response.status_code == 200:
            with open(save_path, 'wb') as f:
                f.write(response.content)
            return f"/images/cbb/{filename}"
    except Exception as e:
        print(f"Couldn't download image for {product_name}: {e}")

    return ""


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

        # --- Kontant / upfront price ---
        kontant_price = None

        # Strategy 1: look for a "Mindstepris inkl. X mdr. abonnement" line — take the LAST number
        mindste_texts = page.locator('text=/Mindstepris/').all_text_contents()
        for raw in mindste_texts:
            raw = raw.replace('\xa0', ' ')
            matches = re.findall(r'(\d{1,3}(?:\.\d{3})+|\d{3,})', raw)
            if matches:
                return int(matches[-1].replace('.', ''))

        # Strategy 2: "Kontant" price block
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
            # Strategy 3: "Betales nu" total
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
            # Strategy 4: largest standalone price-looking number on the page
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
        print(f"  Error scraping {url}: {e}")

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
    os.makedirs(os.path.join(BASE_DIR, 'data/cbb'), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, 'public/images/cbb'), exist_ok=True)

    date_time = datetime.datetime.now().strftime("%d-%m-%Y-%H:%M")
    cleaned_results = []

    # cbb's direct api endpoint for loading phones
    api_url = "https://www.cbb.dk/api/product/load-phones/"
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    print("Fetching product list from CBB API...")
    try:
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        raw_data = response.json()
    except requests.RequestException as e:
        print(f"Couldn't fetch data from CBB API: {e}")
        return

    phones_list = raw_data.get("content", {}).get("phones", [])
    print(f"Found {len(phones_list)} products in JSON")

    is_ci = os.environ.get('CI') == 'true'

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=is_ci)
        context = browser.new_context(
            locale="da-DK",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        # accept cookies up front by injecting consent cookies
        context.add_cookies([
            {"name": "CookieInformationConsent", "value": "true", "domain": ".cbb.dk", "path": "/"},
        ])
        page = context.new_page()

        for phone in phones_list:
            entry = build_entry(phone, page, date_time)
            cleaned_results.append(entry)

        browser.close()

    # save output
    output_path = os.path.join(BASE_DIR, 'data/cbb/cbb_offers.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(cleaned_results, f, ensure_ascii=False, indent=4)

    print(f"\nScraping complete. Saved {len(cleaned_results)} offers to 'cbb_offers.json'")


if __name__ == "__main__":
    scrape_cbb()