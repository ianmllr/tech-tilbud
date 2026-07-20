import requests
from bs4 import BeautifulSoup
import re
from pathlib import Path
from typing import TypedDict
from scraper_utils import download_image_cached, now_timestamp, write_json, log, offer_summary

# setup
BASE_DIR = Path(__file__).resolve().parent.parent
AFFILIATE_PREFIX = "https://go.adt284.net/t/t?a=1666103641&as=2054240298&t=2&tk=1&url="
DATA_DIR = BASE_DIR / "data" / "oister"
IMAGE_DIR = BASE_DIR / "public" / "images" / "oister"
OUTPUT_PATH = DATA_DIR / "oister_offers.json"

# blocked products due to bad naming by oister (will be skipped)
BLOCKED_PRODUCTS = [
    "Robotstøvsuger"
]

SOUND_KEYWORDS = ['urbanista', 'airpods', 'galaxy buds', 'jabra', 'soundcore',
                  'bose', 'jbl', 'headphones', 'høretelefoner', 'earbuds', 'speaker', 'højttaler']
TABLET_KEYWORDS = ['tablet', 'ipad', 'tab']
GAMING_KEYWORDS = ['playstation', 'xbox', 'nintendo', 'controller']


class OfferItem(TypedDict):
    link: str
    product_name: str
    image_url: str
    provider: str
    type: str
    price_without_subscription: int | str
    price_with_subscription: int | str
    min_cost_6_months: int | str
    subscription_price_monthly: int | str
    discount_on_product: int | str
    saved_at: str


def _bs4_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def download_image(image_url, product_name):
    return download_image_cached(
        image_url,
        product_name,
        IMAGE_DIR,
        "/images/oister",
        base_url="https://www.oister.dk",
    )


def product_name_from_url(href: str, fallback_name: str) -> str:
    # the url pattern is always: <subscription-description>-inkl-<product-name>
    slug = href.rstrip('/').split('/')[-1]
    if '-inkl-' in slug:
        product_part = slug.split('-inkl-', 1)[1]
        words = product_part.replace('-', ' ').split()
        return ' '.join(w[0].upper() + w[1:] if w else w for w in words)
    return fallback_name


def guess_type(product_name: str) -> str:
    name_lower = product_name.lower()
    if any(k in name_lower for k in SOUND_KEYWORDS):
        return 'sound'
    if any(k in name_lower for k in TABLET_KEYWORDS):
        return 'tablet'
    if any(k in name_lower for k in GAMING_KEYWORDS):
        return 'gaming'
    return 'phone'


def scrape_oister():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    url = "https://www.oister.dk/tilbehor-til-abonnement"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    response = requests.get(url, headers=headers)
    date_time = now_timestamp()

    if response.status_code != 200:
        log(f"Error! Could not fetch the page. Status code: {response.status_code}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')

    # offers are <a> tags whose href contains '-inkl-' — no wrapping card divs in the new site structure
    offer_links = [
        a for a in soup.find_all('a', href=True)
        if '-inkl-' in a.get('href', '')
           and a.get_text(strip=True)
    ]

    log(f"Found {len(offer_links)} offer <a> tags")

    scraped_data = []
    seen_hrefs: set[str] = set()

    for a_tag in offer_links:
        # product link
        href = _bs4_str(a_tag.get('href'))
        full_link = f"https://www.oister.dk{href}" if href.startswith('/') else href

        # deduplicate — same product may appear in multiple sections
        if full_link in seen_hrefs:
            continue
        seen_hrefs.add(full_link)

        text = a_tag.get_text(separator=' ', strip=True)

        # product name — prefer <strong> tag, fall back to url slug
        strong = a_tag.find('strong')
        if strong:
            raw_name = strong.get_text(strip=True)
            # if the strong text is a generic category label, derive proper name from url
            GENERIC_LABELS = {'tablet', 'headphones', 'høretelefoner', 'earphones',
                              'earbuds', 'speaker', 'højttaler', 'watch', 'ur'}
            last_word = raw_name.split()[-1].lower() if raw_name else ''
            if last_word in GENERIC_LABELS:
                product_name = product_name_from_url(href, raw_name)
                log(f"  Enriched name: '{raw_name}' -> '{product_name}'")
            else:
                product_name = raw_name
        else:
            product_name = product_name_from_url(href, "")

        if not product_name:
            log(f"  Skipping — could not determine product name for {href}")
            continue

        # check blocklist
        name_lower = product_name.lower()
        if any(keyword.lower() in name_lower for keyword in BLOCKED_PRODUCTS):
            matched = next(k for k in BLOCKED_PRODUCTS if k.lower() in name_lower)
            log(f"  Skipping blocked product: {product_name} (matched: '{matched}')")
            continue

        # discount / retail value
        vaerdi_match = re.search(r'Værdi\s*([\d.]+),-\)', text)
        if vaerdi_match:
            discount = int(vaerdi_match.group(1).replace('.', ''))
        else:
            vaerdi_match2 = re.search(r'\(([\d.]+),-\)', text)
            discount = int(vaerdi_match2.group(1).replace('.', '')) if vaerdi_match2 else 0

        # monthly subscription price
        price_match = re.search(r'(\d+)\s*,- /md', text)
        sub_price = int(price_match.group(1)) if price_match else 0

        # min cost over 6 months — read from page if available, otherwise calculate
        min6_match = re.search(r'Min\.\s*pris\s*([\d.]+)\s*kr', text)
        if min6_match:
            min_cost = int(min6_match.group(1).replace('.', ''))
        else:
            min_cost = sub_price * 6 + 99

        # image
        img_tag = a_tag.find('img')
        image_url = ""
        if img_tag:
            src = _bs4_str(img_tag.get('src')) or _bs4_str(img_tag.get('data-src'))
            if src:
                image_url = f"https://www.oister.dk{src}" if src.startswith('/') else src

        image_url = download_image(image_url, product_name)

        item: OfferItem = {
            "link": AFFILIATE_PREFIX + full_link,
            "product_name": product_name,
            "image_url": image_url,
            "provider": "Oister",
            "type": guess_type(product_name),
            "price_without_subscription": discount,
            "price_with_subscription": 0,
            "min_cost_6_months": min_cost,
            "subscription_price_monthly": sub_price,
            "discount_on_product": discount,
            "saved_at": date_time,
        }

        scraped_data.append(item)
        offer_summary(
            item["product_name"],
            sub=item["price_with_subscription"],
            rabat=item["discount_on_product"],
            kontant=item["price_without_subscription"],
            min6=item["min_cost_6_months"],
            md=item["subscription_price_monthly"],
        )

    # save results to JSON file
    write_json(OUTPUT_PATH, scraped_data)
    log(f"Exported {len(scraped_data)} offers to 'data/oister/oister_offers.json'")


if __name__ == "__main__":
    scrape_oister()