import re
from bs4 import BeautifulSoup
from pathlib import Path
from playwright.sync_api import ViewportSize, sync_playwright
from scraper_utils import download_image_cached, now_timestamp, write_json, log, skip_if_blacklisted

# setup
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "telmore"
IMAGE_DIR = BASE_DIR / "public" / "images" / "telmore"
OUTPUT_PATH = DATA_DIR / "telmore_offers.json"
VIEWPORT: ViewportSize = {"width": 1920, "height": 10000}


def download_image(image_url, product_name):
    return download_image_cached(image_url, product_name, IMAGE_DIR, "/images/telmore")


def scrape_detail_page(page, url):
    try:
        page.goto(url, timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        soup = BeautifulSoup(page.content(), 'html.parser')
    except Exception as e:
        log(f"  [WARN] Could not load detail page {url}: {e}")
        return None

    # subscription price is only on detail page. it's almost always 299, but that could change

    subscription_price_monthly = None
    for strong in soup.find_all('strong'):
        m = re.search(r'(\d+)\s*kr\./md', strong.get_text(strip=True), re.IGNORECASE)
        if m:
            subscription_price_monthly = int(m.group(1))
            break
    return subscription_price_monthly


def scrape_telmore():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    url = "https://www.telmore.dk/shop/mobiltelefoner"
    date_time = now_timestamp()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        # very tall viewport to load images for all products
        page = browser.new_page(viewport=VIEWPORT)
        try:
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_selector('div.carousel-image-wrapper')
            page.wait_for_timeout(3000)
        except Exception as e:
            log(f"[WARN] Could not load Telmore listing page {url}: {e}")
            browser.close()
            write_json(OUTPUT_PATH, [])
            log("Exported 0 offers due to page load failure")
            return
        html = page.content()

        soup = BeautifulSoup(html, 'html.parser')
        offer_list = soup.find_all('div', class_='col-md-6 col-12')
        scraped_data = []

        for offer in offer_list:
            item = {
                "link": "",
                "product_name": "",
                "image_url": "",
                "provider": "Telmore",
                "type": "phone",
                "price_without_subscription": "",
                "price_with_subscription": "",
                "subscription_price_monthly": "",
                "discount_on_product": "",
                "min_cost_6_months": "",
                "saved_at": date_time
            }

            # product link
            link_div = offer.find('div', class_='mb-4')
            if link_div:
                link_tag = link_div.find('a')
                if link_tag:
                    href = link_tag.get('href')
                    if href:
                        item["link"] = f"https://www.telmore.dk{href}" if href.startswith('/') else href

            # product name
            name_tag = offer.find('strong', class_='h4')
            if name_tag:
                item["product_name"] = name_tag.get_text(strip=True)

            # image url
            img_div = offer.find('div', class_='carousel-image-wrapper')
            if img_div:
                img_tag = img_div.find('img')
                if img_tag:
                    src_url = img_tag.get('src')
                    if src_url:
                        item["image_url"] = f"https:{src_url}" if src_url.startswith('//') else src_url

            item["image_url"] = download_image(item["image_url"], item["product_name"])

            # price with subscription
            price_tag = offer.find('span', class_='tlm-product-list-card__price')
            if price_tag:
                price_val = "".join(re.findall(r'\d+', price_tag.get_text()))
                if price_val:
                    item["price_with_subscription"] = int(price_val)

            # discount
            discount_span = offer.find('span', string=re.compile(r'Mobilrabat', re.IGNORECASE))
            if discount_span:
                discount_val = "".join(re.findall(r'\d+', discount_span.get_text()))
                if discount_val:
                    item["discount_on_product"] = int(discount_val)

            # min price
            min_price_span = offer.find('span', string=re.compile(r'Mindstepris', re.IGNORECASE))
            if min_price_span:
                min_val = "".join(re.findall(r'\d+', min_price_span.get_text()))
                if min_val:
                    item["min_cost_6_months"] = int(min_val)

            # calculate price without subscription
            if item["price_with_subscription"] and item["discount_on_product"]:
                item["price_without_subscription"] = item["price_with_subscription"] + item["discount_on_product"]

            # subscription monthly price — requires visiting detail page
            if item["link"]:
                item["subscription_price_monthly"] = scrape_detail_page(page, item["link"])

            if skip_if_blacklisted(item["product_name"]):
                continue

            def fmt(value):
                return value if value not in (None, "") else "-"

            scraped_data.append(item)
            log(
                f"  {item['product_name']}: "
                f"sub={fmt(item['price_with_subscription'])}, "
                f"rabat={fmt(item['discount_on_product'])}, "
                f"kontant={fmt(item['price_without_subscription'])}, "
                f"min6={fmt(item['min_cost_6_months'])}, "
                f"md={fmt(item['subscription_price_monthly'])}"
            )

        browser.close()

    # save results to JSON file
    write_json(OUTPUT_PATH, scraped_data)

    log(f"Exported {len(scraped_data)} offers")


if __name__ == "__main__":
    scrape_telmore()