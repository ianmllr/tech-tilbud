import re
from pathlib import Path
from playwright.sync_api import ViewportSize, sync_playwright
from scraper_utils import download_image_cached, now_timestamp, write_json, log

BASE_DIR  = Path(__file__).parent.parent
IMAGE_DIR = BASE_DIR / "public" / "images" / "norlys"
DATA_DIR  = BASE_DIR / "data" / "norlys"
VIEWPORT: ViewportSize = {"width": 1920, "height": 1080}

SHOP_BASE   = "https://shop.norlys.dk"
CONTEXT_ID  = "326701"
INSTALLMENT = "1"

CATEGORY_URLS: dict[str, str] = {
    f"{SHOP_BASE}/privat/webshop/mobiler/": "phone",
    f"{SHOP_BASE}/privat/webshop/tablets/":  "tablet",
    f"{SHOP_BASE}/privat/webshop/tilbehoer/kategori/tilbehoer-med-abonnement/?ProductType=WiFi%20Tablets%3BAccessories_-_Category_352948": "tablet",
#    f"{SHOP_BASE}/privat/webshop/tilbehoer/kategori/tilbehoer-med-abonnement/?ProductType=Gaming%3BAccessories_-_Category_352948": "gaming",
    f"{SHOP_BASE}/privat/webshop/tilbehoer/kategori/tilbehoer-med-abonnement/?ProductType=Smartwatches%3BAccessories_-_Category_352948":  "smartwatch",
    f"{SHOP_BASE}/privat/webshop/tilbehoer/kategori/tilbehoer-med-abonnement/?ProductType=Høretelefoner%2Fheadsets%3BAccessories_-_Category_352948":  "audio",
    f"{SHOP_BASE}/privat/webshop/tilbehoer/kategori/tilbehoer-med-abonnement/?ProductType=Højtalere%3BAccessories_-_Category_352948":  "audio",
}

MAX_SUBSCRIPTIONS = 5

# blacklisted names. this is needed because norlys offers gaming laptops which are very poorly named making them extremely hard
# to reliably extract price data from. these products can not be filtered out by scraping specific parts of the site
# because they are hidden on even non-gaming related pages.
PRODUCT_BLACKLIST = [
    "loq",
    "bærbar",
    "legion"
]


def is_product_blacklisted(product_name: str) -> bool:
    """Check if a product name matches any blacklist entry."""
    product_lower = product_name.lower()
    for blacklist_item in PRODUCT_BLACKLIST:
        if blacklist_item.lower() in product_lower:
            return True
    return False


def normalize_product_name(product_name: str) -> str:
    # Strip subscription suffix from Norlys display titles.
    cleaned = re.sub(r"\(\s*med\s+abonnement\s*\)", "", product_name, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bmed\s+abonnement\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\s+-\s+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"^[-,]\s*", "", cleaned)
    cleaned = re.sub(r"\s*[-,]$", "", cleaned)
    return cleaned

def download_image(image_url: str, product_name: str) -> str:
    return download_image_cached(
        image_url,
        product_name,
        IMAGE_DIR,
        "/images/norlys",
        base_url=SHOP_BASE,
    )




def get_product_links_from_listing(page, cat_url: str) -> list[str]:
    try:
        page.goto(cat_url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2500)
    except Exception as e:
        log(f"  Could not load {cat_url}: {e}")
        return []

    links: list[str] = []
    seen: set[str] = set()

    for a in page.query_selector_all('a[href*="/shop/"]'):
        href = a.get_attribute("href") or ""
        # only device product pages: /shop/{brand}/{slug}/#/{color}/{storage}/1
        if re.search(r"/shop/[^/]+/[^/]+/#/", href):
            slug = re.sub(r"/#/.*$", "/", href)  # canonical slug without color/storage
            if slug not in seen:
                seen.add(slug)
                links.append(href)

    log(f"  Found {len(links)} unique products on {cat_url}")
    return links


def extract_price_data(price: dict) -> dict | None:
    # extract the relevant price fields from a variant API price object.
    min_price     = (price.get("minimumPrice") or {}).get("value")
    monthly_price = (price.get("bundleMonthlyPrice") or {}).get("value")
    product_price = (price.get("productPrice") or {}).get("value")


    if min_price is None or monthly_price is None or product_price is None:
        return None

    return {
        "min_cost_6_months":          min_price,
        "subscription_price_monthly": monthly_price,
        "price_with_subscription":    product_price,
        "price_without_subscription": (price.get("productBasePrice") or {}).get("value"),
        "discount_on_product":        (price.get("productDiscountedPrice") or {}).get("value"),
    }


def scrape_product(page, href: str, product_type: str, saved_at: str) -> dict | None:
    product_url = SHOP_BASE + href if href.startswith("/") else href

    # collect all variant API responses: one fires on page load (site pre-selects the
    # cheapest subscription), then one per subscription card click.
    api_responses: list[dict] = []

    def handle_response(response):
        if "/api/olympus/commerce/catalog/products/variant/" in response.url:
            try:
                api_responses.append(response.json())
            except Exception:
                pass

    page.on("response", handle_response)

    try:
        page.goto(product_url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)
    except Exception as e:
        log(f"  Could not load {product_url}: {e}")
        page.remove_listener("response", handle_response)
        return None

    if not api_responses:
        log(f"  No variant API response captured for {href}")
        page.remove_listener("response", handle_response)
        return None

    initial_data = api_responses[0]
    display_name = initial_data.get("displayName", "")
    raw_product_name = display_name or href.rstrip("/").split("/")[-1].replace("-", " ").title()
    product_name = normalize_product_name(raw_product_name)

    image_urls = initial_data.get("imageUrls", [])
    raw_image  = image_urls[0] if image_urls else ""
    if raw_image.startswith("/"):
        raw_image = SHOP_BASE + raw_image
    local_image = download_image(raw_image, product_name)

    page.remove_listener("response", handle_response)

    best: dict | None = None

    for data in api_responses:
        price = data.get("price") or {}
        entry = extract_price_data(price)
        if entry is None:
            continue
        if best is None or entry["min_cost_6_months"] < best["min_cost_6_months"]:
            best = entry

    if not best:
        log(f"  No valid subscription data for {product_name}")
        return None

    log(
        f"  {product_name}: "
        f"kontant={best['price_without_subscription']}, "
        f"sub={best['price_with_subscription']}, "
        f"rabat={best['discount_on_product']}, "
        f"min6={best['min_cost_6_months']}, "
        f"md={best['subscription_price_monthly']}"
    )

    return {
        "link":                       product_url,
        "product_name":               product_name,
        "image_url":                  local_image,
        "type":                       product_type,
        "price_without_subscription": best["price_without_subscription"],
        "price_with_subscription":    best["price_with_subscription"],
        "discount_on_product":        best["discount_on_product"],
        "min_cost_6_months":          best["min_cost_6_months"],
        "subscription_price_monthly": best["subscription_price_monthly"],
        "saved_at":                   saved_at,
    }



def scrape_norlys():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    saved_at   = now_timestamp()
    all_offers = []
    seen_slugs: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport=VIEWPORT,
            locale="da-DK",
        )
        page = context.new_page()

        # accept cookies once on the homepage
        log("Accepting cookies...")
        page.goto(SHOP_BASE, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)
        try:
            page.click("button.coi-banner__accept", timeout=4000)
            page.wait_for_timeout(1200)
            log("  Cookies accepted")
        except Exception:
            pass

        for cat_url, product_type in CATEGORY_URLS.items():
            log(f"\nScraping category: {cat_url} (type={product_type})")

            product_hrefs = get_product_links_from_listing(page, cat_url)

            for href in product_hrefs:
                slug = re.sub(r"/#/.*$", "/", href)
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)

                offer = scrape_product(page, href, product_type, saved_at)
                if offer and not is_product_blacklisted(offer.get("product_name", "")):
                    all_offers.append(offer)

        context.close()
        browser.close()

    output_path = DATA_DIR / "norlys_offers.json"
    write_json(output_path, all_offers)

    log(f"\nDone. Saved {len(all_offers)} offers to '{output_path}'")


if __name__ == "__main__":
    scrape_norlys()

