import json
import re
import datetime
import requests
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_DIR  = Path(__file__).parent.parent
IMAGE_DIR = BASE_DIR / "public" / "images" / "norlys"
DATA_DIR  = BASE_DIR / "data" / "norlys"

SHOP_BASE   = "https://shop.norlys.dk"
CONTEXT_ID  = "326701"
INSTALLMENT = "1"

CATEGORY_URLS: dict[str, str] = {
    f"{SHOP_BASE}/privat/webshop/mobiler/": "phone",
    f"{SHOP_BASE}/privat/webshop/tablets/":  "tablet",
}

MAX_SUBSCRIPTIONS = 5


def download_image(image_url: str, product_name: str) -> str:
    if not image_url or not product_name:
        return ""

    filename = re.sub(r"[^a-z0-9]", "_", product_name.lower()) + ".webp"
    save_path = IMAGE_DIR / filename
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    if save_path.exists():
        return f"/images/norlys/{filename}"

    # Norlys images may be relative paths
    if image_url.startswith("/"):
        image_url = SHOP_BASE + image_url

    try:
        response = requests.get(image_url, timeout=15)
        if response.status_code == 200:
            save_path.write_bytes(response.content)
            return f"/images/norlys/{filename}"
    except Exception as e:
        print(f"  Could not download image for '{product_name}': {e}")

    return ""




def get_product_links_from_listing(page, cat_url: str) -> list[str]:
    try:
        page.goto(cat_url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2500)
    except Exception as e:
        print(f"  Could not load {cat_url}: {e}")
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

    print(f"  Found {len(links)} unique products on {cat_url}")
    return links


def extract_price_data(price: dict) -> dict | None:
    """Extract the relevant price fields from a variant API price object."""
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
        print(f"  Could not load {product_url}: {e}")
        page.remove_listener("response", handle_response)
        return None

    if not api_responses:
        print(f"  No variant API response captured for {href}")
        page.remove_listener("response", handle_response)
        return None

    initial_data = api_responses[0]
    display_name = initial_data.get("displayName", "")
    product_name = display_name or href.rstrip("/").split("/")[-1].replace("-", " ").title()

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
        print(f"  No valid subscription data for {product_name}")
        return None

    print(
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

    saved_at   = datetime.datetime.now().strftime("%d-%m-%Y-%H:%M")
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
            viewport={"width": 1920, "height": 1080},
            locale="da-DK",
        )
        page = context.new_page()

        # accept cookies once on the homepage
        print("Accepting cookies...")
        page.goto(SHOP_BASE, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)
        try:
            page.click("button.coi-banner__accept", timeout=4000)
            page.wait_for_timeout(1200)
            print("  Cookies accepted")
        except Exception:
            pass

        for cat_url, product_type in CATEGORY_URLS.items():
            print(f"\nScraping category: {cat_url} (type={product_type})")

            product_hrefs = get_product_links_from_listing(page, cat_url)

            for href in product_hrefs:
                slug = re.sub(r"/#/.*$", "/", href)
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)

                offer = scrape_product(page, href, product_type, saved_at)
                if offer:
                    all_offers.append(offer)

        context.close()
        browser.close()

    output_path = DATA_DIR / "norlys_offers.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_offers, f, ensure_ascii=False, indent=4)

    print(f"\nDone. Saved {len(all_offers)} offers to '{output_path}'")


if __name__ == "__main__":
    scrape_norlys()

