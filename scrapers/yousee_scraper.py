import re
import dataclasses
from pathlib import Path
from playwright.sync_api import ViewportSize, sync_playwright
from scraper_utils import download_image_cached, now_timestamp, write_json, log, skip_if_blacklisted

BASE_DIR  = Path(__file__).parent.parent
IMAGE_DIR = BASE_DIR / "public" / "images" / "yousee"
DATA_DIR  = BASE_DIR / "data" / "yousee"
VIEWPORT: ViewportSize = {"width": 1920, "height": 1080}

BASE_URL = "https://yousee.dk"

# Phone listing pages filtered by storage size on the site
# we scrape it this way because storage is not actually listed on the product cards, but it can be inferred by
# using the site filters to isolate phones with specific storage options, and then appending the storage label to the
# product name during extraction
PHONE_STORAGE_URLS: dict[str, str] = {
    # f"{BASE_URL}/shop/mobiltelefoner?sort=popularity-asc&installments=none&memory=128": "128GB",
    # f"{BASE_URL}/shop/mobiltelefoner?sort=popularity-asc&installments=none&memory=256": "256GB",
    # f"{BASE_URL}/shop/mobiltelefoner?sort=popularity-asc&installments=none&memory=512": "512GB",
    # f"{BASE_URL}/shop/mobiltelefoner?sort=popularity-asc&installments=none&memory=1000": "1TB",
}

# Other category listing pages mapped to product type
CATEGORY_URLS: dict[str, str] = {
    f"{BASE_URL}/shop/mobiltelefoner": "phone",
    f"{BASE_URL}/shop/tablets": "tablet",
    f"{BASE_URL}/shop/watches": "watch",
}

# Cookie-consent button selector (same across yousee.dk)
COOKIE_ACCEPT_SELECTOR = 'button[id*="accept"]'


@dataclasses.dataclass
class Offer:
    link: str
    product_name: str
    image_url: str
    provider: str = "YouSee"
    type: str = "phone"
    data_gb: str = ""
    price_without_subscription: int | None = None
    price_with_subscription: int | None = None
    discount_on_product: int | None = None
    min_cost_6_months: int | None = None
    subscription_price_monthly: int | None = None
    saved_at: str = ""


def parse_price(text: str) -> int | None:
    # return price
    if not text:
        return None
    digits = re.sub(r"\D", "", text.replace(".", ""))
    return int(digits) if digits else None


def download_image(image_url: str, product_name: str) -> str:
    # download image if it doesn't exist
    return download_image_cached(image_url, product_name, IMAGE_DIR, "/images/yousee")


def accept_cookies(page) -> None:
    try:
        page.click(COOKIE_ACCEPT_SELECTOR, timeout=4000)
        page.wait_for_timeout(1200)
    except Exception:
        pass  # banner may already be dismissed



def extract_card(card, product_type: str, saved_at: str, storage_label: str = "") -> "Offer | None":
    # product link – prefer the name-link anchor, fall back to image anchor
    link_el = card.query_selector("a.product-card__name-link")
    if not link_el:
        link_el = card.query_selector("a.product-card__image")
    href = link_el.get_attribute("href") if link_el else ""
    # strip query parameters (e.g. ?installments=none) for a clean canonical URL
    href = href.split("?")[0] if href else ""
    product_link = (f"{BASE_URL}{href}" if href.startswith("/") else href) if href else ""

    # product name (manufacturer + model), optionally with a storage suffix from the filtered page
    manufacturer_el = card.query_selector("span.product-card__subname.taProductCardSubname")
    manufacturer_name = manufacturer_el.inner_text().strip() if manufacturer_el else ""

    name_el = card.query_selector("h3.taProductCardName, h3.product-card__name")
    name_text = name_el.inner_text().strip() if name_el else ""
    base_product_name = " ".join(part for part in [manufacturer_name, name_text] if part)
    product_name = " ".join(part for part in [base_product_name, storage_label] if part)
    if not product_name:
        return None

    # product image URL (thumbnail from listing; query params stripped for higher res)
    img_el = card.query_selector("div.product-card__image-wrapper img")
    raw_image_url = img_el.get_attribute("src") if img_el else ""
    raw_image_url = raw_image_url.split("?")[0] if raw_image_url else ""
    if raw_image_url.startswith("//"):
        raw_image_url = "https:" + raw_image_url

    local_image_path = download_image(raw_image_url, base_product_name or product_name)

    # price with subscription (the large bold number, e.g. "4.399")
    price_el = card.query_selector("div.price._small._bold span._huge._bold")
    price_with_subscription = parse_price(price_el.inner_text()) if price_el else None

    # discount / rabat  (e.g. "1.100 kr." next to "Rabat" label)
    discount_el = card.query_selector("div.product-card__discount div.price span")
    discount_on_product = parse_price(discount_el.inner_text()) if discount_el else None

    # price without subscription = price_with_subscription + discount
    if price_with_subscription is not None and discount_on_product is not None:
        price_without_subscription = price_with_subscription + discount_on_product
    else:
        price_without_subscription = price_with_subscription  # no discount listed

    # min cost over 6 months ("Mindstepris 6 mdr. X.XXX kr.")
    min_price_el = card.query_selector("div.product-card__min-price")
    min_cost_6_months = None
    if min_price_el:
        raw_min = min_price_el.inner_text()
        # grab the last number to avoid picking up "6" from "6 mdr."
        nums = re.findall(r"(\d{1,3}(?:\.\d{3})+|\d{4,})", raw_min)
        if nums:
            min_cost_6_months = int(nums[-1].replace(".", ""))

    # monthly price derived from: (min_cost_6_months - price_with_subscription) / 6
    subscription_price_monthly = None
    if min_cost_6_months is not None and price_with_subscription is not None:
        subscription_price_monthly = round((min_cost_6_months - price_with_subscription) / 6)

    log(
        f"  {product_name}: "
        f"sub={price_with_subscription}, "
        f"rabat={discount_on_product}, "
        f"kontant={price_without_subscription}, "
        f"min6={min_cost_6_months}, "
        f"md={subscription_price_monthly}"
    )

    return Offer(
        link=product_link,
        product_name=product_name,
        image_url=local_image_path,
        type=product_type,
        data_gb=storage_label,
        price_with_subscription=price_with_subscription,
        price_without_subscription=price_without_subscription,
        discount_on_product=discount_on_product,
        min_cost_6_months=min_cost_6_months,
        subscription_price_monthly=subscription_price_monthly,
        saved_at=saved_at,
    )


def scrape_listing_page(page, cat_url: str, product_type: str, saved_at: str, seen_names: set[str], all_offers: list[Offer], storage_label: str = "") -> None:
    log(f"\nScraping category: {cat_url} (type={product_type})")

    try:
        page.goto(cat_url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2500)
    except Exception as e:
        log(f"  Could not load {cat_url}: {e}")
        return

    # Dismiss cookie banner again in case it reappeared
    accept_cookies(page)

    # Scroll to bottom to ensure all lazy-loaded cards are rendered
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(1500)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(500)

    # All product cards carry the taProductCard marker class
    cards = page.query_selector_all('div[class*="taProductCard"]')
    log(f"  Found {len(cards)} product cards")

    for card in cards:
        offer = extract_card(card, product_type, saved_at, storage_label)
        if offer and offer.product_name and offer.product_name not in seen_names and not skip_if_blacklisted(offer.product_name):
            seen_names.add(offer.product_name)
            all_offers.append(offer)



def scrape_yousee():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    saved_at   = now_timestamp()
    all_offers: list[Offer] = []
    seen_names: set[str]    = set()

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

        # Accept cookies once on the homepage so the banner doesn't reappear
        log("Accepting cookies on homepage...")
        page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)
        accept_cookies(page)

        # scrape phone listing pages once per storage size
        for cat_url, storage_label in PHONE_STORAGE_URLS.items():
            scrape_listing_page(page, cat_url, "phone", saved_at, seen_names, all_offers, storage_label)

        # scrape the remaining category listing pages
        for cat_url, product_type in CATEGORY_URLS.items():
            scrape_listing_page(page, cat_url, product_type, saved_at, seen_names, all_offers)

        context.close()
        browser.close()

    # save results
    output_path = DATA_DIR / "yousee_offers.json"
    write_json(output_path, [dataclasses.asdict(o) for o in all_offers])

    log(f"\nDone. Saved {len(all_offers)} offers to '{output_path}'")


if __name__ == "__main__":
    scrape_yousee()

