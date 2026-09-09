import re
from bs4 import BeautifulSoup
from pathlib import Path
from playwright.sync_api import ViewportSize, sync_playwright
from scraper_utils import download_image_cached, now_timestamp, write_json, log, offer_summary, skip_if_blacklisted

BASE_DIR = Path(__file__).resolve().parent.parent
BASE_URL = "https://www.telmore.dk"
DATA_DIR = BASE_DIR / "data" / "telmore"
IMAGE_DIR = BASE_DIR / "public" / "images" / "telmore"
OUTPUT_PATH = DATA_DIR / "telmore_tilgift_offers.json"
VIEWPORT: ViewportSize = {"width": 1920, "height": 1080}


def download_image(image_url, product_name):
    return download_image_cached(image_url, product_name, IMAGE_DIR, "/images/telmore")


def _bs4_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def scrape_detail_page(page, url):
    page.goto(url, timeout=60000, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    html = page.content()
    soup = BeautifulSoup(html, 'html.parser')

    min_cost_6_months = None
    discount_on_product = None
    subscription_price_monthly = None
    image_url = ""

    # min cost
    for p in soup.find_all('p'):
        classes = p.get('class') or []
        if 'text--xs' in classes or 'mb-0' in classes:
            text = p.get_text(strip=True)
            m = re.search(r'Mindstepris\s*:\s*(\d[\d.]*)\s*kr', text, re.IGNORECASE)
            if m:
                min_cost_6_months = int(m.group(1).replace('.', ''))
            else:
                m = re.search(r'Mindstepris\s+kr\.?\s*:\s*(\d+)', text, re.IGNORECASE)
                if m:
                    min_cost_6_months = int(m.group(1))
            break

    # discount
    for span in soup.find_all('span'):
        span_text = span.get_text(strip=True)
        if re.search(r'Mobilrabat|Rabat', span_text, re.IGNORECASE):
            discount_val = "".join(re.findall(r'\d+', span_text))
            if discount_val:
                discount_on_product = int(discount_val)
            break

    # subscription monthly price
    for strong in soup.find_all('strong'):
        t = strong.get_text(strip=True)
        m = re.search(r'(\d+)\s*kr\./md', t, re.IGNORECASE)
        if m:
            subscription_price_monthly = int(m.group(1))
            break

    # img
    for img_tag in soup.find_all('img'):
        classes = img_tag.get('class') or []
        if 'product-list-card-campaign-image' in classes:
            src = _bs4_str(img_tag.get('src', ''))
            image_url = 'https:' + src if src.startswith('//') else src
            break
    # Fallback
    if not image_url:
        for img in soup.find_all('img'):
            src = _bs4_str(img.get('src', ''))
            if 'ctfassets' in src:
                image_url = src
                break

    return min_cost_6_months, discount_on_product, subscription_price_monthly, image_url


def scrape_telmore_tilgift():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    listing_url = "https://www.telmore.dk/shop/tilgift/"
    date_time = now_timestamp()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        page = browser.new_page(viewport=VIEWPORT)

        # scrape listing page
        log(f"Loading listing: {listing_url}")
        page.goto(listing_url, timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        listing_html = page.content()
        soup = BeautifulSoup(listing_html, 'html.parser')

        cards = soup.find_all('div', class_='tlm-product-list-card')
        log(f"Found {len(cards)} tilgift offers")

        scraped_data = []

        for card in cards:
            name_tag = card.find('strong', class_='h4')
            brand_tag = card.find('span', class_='gray--text')
            price_tag = card.find('span', class_='tlm-product-list-card__price')
            link_tag = card.find('a', href=True)

            product_name = _bs4_str(name_tag.get_text(strip=True)) if name_tag else ""
            brand = _bs4_str(brand_tag.get_text(strip=True)) if brand_tag else ""
            full_name = product_name
            if brand:
                full_name = f"{brand} {product_name}".strip()

            # Product price (what you pay for the gift item)
            product_price = None
            if price_tag:
                price_text = _bs4_str(price_tag.get_text(strip=True))
                val = "".join(re.findall(r'\d+', price_text))
                if val:
                    product_price = int(val)

            href = _bs4_str(link_tag.get('href')) if link_tag else ""
            detail_url = (BASE_URL + href) if href.startswith('/') else href

            min_cost_6_months, discount_on_product, subscription_price_monthly, image_url = scrape_detail_page(page, detail_url)

            # Download image
            local_image = download_image(image_url, full_name) if image_url else ""

            name_lower = full_name.lower()
            if "tab" in name_lower:
                product_type = "tablet"
            elif "beoplay" in name_lower or "airpods" in name_lower:
                product_type = "sound"
            else:
                product_type = "gift"

            # price_with_subscription = what you pay for the gift item
            price_with_subscription = product_price
            # price_without_subscription = gift price before subscription discount
            price_without_subscription = None
            if price_with_subscription is not None and discount_on_product is not None:
                price_without_subscription = price_with_subscription + discount_on_product

            item = {
                "link": detail_url,
                "product_name": full_name,
                "image_url": local_image,
                "provider": "Telmore",
                "type": product_type,
                "price_without_subscription": price_without_subscription,
                "price_with_subscription": price_with_subscription,
                "subscription_price_monthly": subscription_price_monthly,
                "discount_on_product": discount_on_product,
                "min_cost_6_months": min_cost_6_months,
                "saved_at": date_time
            }

            if skip_if_blacklisted(full_name):
                continue

            scraped_data.append(item)
            offer_summary(
                full_name,
                sub=price_with_subscription,
                rabat=discount_on_product,
                kontant=price_without_subscription,
                min6=min_cost_6_months,
                md=subscription_price_monthly,
            )

        browser.close()

    write_json(OUTPUT_PATH, scraped_data)

    log(f"\nExported {len(scraped_data)} tilgift offers to {OUTPUT_PATH}")


if __name__ == "__main__":
    scrape_telmore_tilgift()

