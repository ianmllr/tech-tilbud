import re
import dataclasses
import os
from pathlib import Path
from playwright.sync_api import ViewportSize, sync_playwright
from scraper_utils import download_image_cached, now_timestamp, write_json, log, offer_summary


BASE_DIR = Path(__file__).parent.parent
IMAGE_DIR = BASE_DIR / "public" / "images" / "3"
DATA_DIR  = BASE_DIR / "data" / "3"
VIEWPORT: ViewportSize = {"width": 1920, "height": 1080}

BASE_URL = "https://www.3.dk"

CATEGORY_URLS: dict[str, str] = {
    f"{BASE_URL}/shop/mobiler/": "phone",
    f"{BASE_URL}/shop/tablets/": "tablet",
}

CONSENT_COOKIES = [
    {"name": "cookieconsent_status", "value": "allow", "domain": ".3.dk", "path": "/"},
    {
        "name": "CookieConsent",
        "value": "{stamp:%27*%27%2Cnecessary:true%2Cpreferences:true%2Cstatistics:true%2Cmarketing:true%2Cver:1}",
        "domain": ".3.dk",
        "path": "/",
    },
]

IMAGE_SKIP_KEYWORDS = [
    "logo", "badge", "award", "mobilsiden", "sticker", "icon", "sprite",
    "trustpilot", "payment", "flag", "ribbon", "stamp", "anbefalet",
]

HEADLESS = os.environ.get("CI") == "true"

KR_PATTERN = r"(\d{1,3}(?:\.\d{3})+|\d{4,})[\s\xa0]*kr"


@dataclasses.dataclass
class Offer:
    link: str
    product_name: str
    image_url: str
    provider: str = "3"
    type: str = "phone"
    signup_price: int = 0
    data_gb: int = 0
    price_without_subscription: int | None = None
    price_with_subscription: int | None = None
    discount_on_product: int = 0
    min_cost_6_months: int | None = None
    subscription_price_monthly: int | None = None
    saved_at: str = ""


def parse_price(text: str) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[\D.]", "", text)
    return int(digits) if digits else None


def download_image(image_url: str, product_name: str) -> str:
    return download_image_cached(image_url, product_name, IMAGE_DIR, "/images/3")


def find_product_image(page) -> str:
    for el in page.query_selector_all("picture img"):
        src = (el.get_attribute("src") or "").lower()
        alt = (el.get_attribute("alt") or "").lower()
        if not src or src.endswith(".svg"):
            continue
        if any(kw in src or kw in alt for kw in IMAGE_SKIP_KEYWORDS):
            continue
        src = el.get_attribute("src") or ""
        if src.startswith("//"): return "https:" + src
        if src.startswith("/"): return BASE_URL + src
        return src
    return ""


def get_storrelse_row_text(page) -> str:
    """Returns the text content of the ancestor container of the Størrelse label."""
    storrelse_el = page.locator("text=/Størrelse/").first
    if not storrelse_el.count():
        return ""
    return storrelse_el.locator("xpath=ancestor::*[3]").first.text_content() or ""


def extract_storage_label(row_text: str, product_name: str) -> str:
    m = re.search(r"(\d+\s*(?:GB|TB))", row_text, re.IGNORECASE)
    if m:
        return m.group(1).replace(" ", "")
    m = re.search(r"(\d+\s*GB)", product_name, re.IGNORECASE)
    return m.group(1).replace(" ", "") if m else ""


def extract_upfront_price(row_text: str) -> int | None:
    clean = re.sub(r"Mobilrabat[\s\xa0]*[\d.]+[\s\xa0]*kr\.?", "", row_text, flags=re.IGNORECASE)
    match = re.search(KR_PATTERN, clean)
    return int(match.group(1).replace(".", "")) if match else None


def extract_subscription_info(page) -> tuple[int | None, int | None, int | None]:
    """Returns (discount_on_product, subscription_price_monthly, min_cost_6_months)."""
    discount_on_product = None
    mobilrabat_el = page.locator("text=/Mobilrabat/").first
    if mobilrabat_el.count():
        discount_on_product = parse_price(mobilrabat_el.text_content() or "")

    subscription_price_monthly = None
    for text in page.locator(r"text=/\d+\s*kr\.?\/md/").all_text_contents():
        match = re.search(r"([\d.]+)\s*kr\.?/md", text)
        if match:
            subscription_price_monthly = int(match.group(1).replace(".", ""))
            break

    min_cost_6_months = None
    mindste_el = page.locator("text=/Mindstepris/").first
    if mindste_el.count():
        raw = mindste_el.text_content() or ""
        # Prefer explicit price matches that include 'kr' to avoid picking up the
        # stray '6' from '6 mdr.' or other single-digit tokens.
        m = re.search(KR_PATTERN, raw)
        if m:
            min_cost_6_months = int(m.group(1).replace(".", ""))
        else:
            # Fallback: look for multi-digit numbers (ignore isolated single-digit "6")
            numbers = re.findall(r"(\d{1,3}(?:\.\d{3})+|\d{2,})", raw)
            if numbers:
                min_cost_6_months = int(numbers[-1].replace(".", ""))

    return discount_on_product, subscription_price_monthly, min_cost_6_months


def scrape_product_page(page, url: str, saved_at: str, product_type: str = "phone") -> Offer | None:
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1500)
    except Exception as e:
        log(f"  [WARN] Could not load {url}: {e}")
        return None

    name_el = page.query_selector("h1")
    product_name = name_el.inner_text().strip() if name_el else ""
    if not product_name:
        log(f"  [WARN] No product name found at {url}")
        return None

    row_text = get_storrelse_row_text(page)
    storage_label = extract_storage_label(row_text, product_name)
    full_name = f"{product_name} {storage_label}".strip() if storage_label else product_name

    price_with_subscription = extract_upfront_price(row_text)
    discount_on_product, subscription_price_monthly, min_cost_6_months = extract_subscription_info(page)

    if not min_cost_6_months and price_with_subscription and subscription_price_monthly:
        min_cost_6_months = price_with_subscription + (6 * subscription_price_monthly)

    price_without_subscription = None
    if price_with_subscription is not None and discount_on_product is not None:
        price_without_subscription = (
            price_with_subscription + discount_on_product
        )

    image_url = find_product_image(page)
    local_image_path = download_image(image_url, full_name)

    offer_summary(
        full_name,
        sub=price_with_subscription,
        rabat=discount_on_product,
        kontant=price_without_subscription,
        min6=min_cost_6_months,
        md=subscription_price_monthly,
    )

    return Offer(
        link=url,
        product_name=full_name,
        image_url=local_image_path,
        type=product_type,
        price_with_subscription=price_with_subscription,
        discount_on_product=discount_on_product or 0,
        min_cost_6_months=min_cost_6_months,
        subscription_price_monthly=subscription_price_monthly,
        saved_at=saved_at,
    )


def collect_product_links(page) -> list[tuple[str, str]]:
    seen_urls: set[str] = set()
    product_links: list[tuple[str, str]] = []

    for cat_url, product_type in CATEGORY_URLS.items():
        log(f"Scanning category: {cat_url}")
        try:
            page.goto(cat_url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
        except Exception as e:
            log(f"  [WARN] Could not load {cat_url}: {e}")
            continue

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)

        link_selector = 'a[href*="/shop/mobiler/"], a[href*="/shop/tablets/"]'
        for anchor in page.query_selector_all(link_selector):
            href = anchor.get_attribute("href") or ""
            # Require at least 5 path segments to exclude brand/category pages
            if href.count("/") >= 5 and "?" not in href:
                full_url = f"{BASE_URL}{href}" if href.startswith("/") else href
                if full_url not in seen_urls:
                    seen_urls.add(full_url)
                    product_links.append((full_url, product_type))

    return product_links


def scrape_3():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    saved_at = now_timestamp()
    all_offers: list[Offer] = []
    seen_names: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport=VIEWPORT,
            locale="da-DK",
        )
        context.add_cookies(CONSENT_COOKIES)  # type: ignore[arg-type]
        page = context.new_page()

        product_links = collect_product_links(page)
        log(f"\nFound {len(product_links)} unique product pages\n")

        for url, product_type in product_links:
            log(f"Scraping: {url}")
            offer = scrape_product_page(page, url, saved_at, product_type)
            if offer and offer.product_name not in seen_names and "brugt" not in offer.product_name.lower():
                seen_names.add(offer.product_name)
                all_offers.append(offer)

    output_path = DATA_DIR / "3_offers.json"
    write_json(output_path, [dataclasses.asdict(o) for o in all_offers])

    log(f"\nDone. Saved {len(all_offers)} offers to '{output_path}'")


if __name__ == "__main__":
    scrape_3()

