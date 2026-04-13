import json
import datetime
import re
import requests
import os
from pathlib import Path
from typing import TYPE_CHECKING
from playwright.sync_api import sync_playwright

if TYPE_CHECKING:
    from playwright._impl._api_structures import SetCookieParam


BASE_DIR = Path(__file__).resolve().parent.parent
CALLME_DATA_DIR = BASE_DIR / "data" / "callme"
CALLME_IMAGE_DIR = BASE_DIR / "public" / "images" / "callme"
CALLME_OUTPUT_FILE = CALLME_DATA_DIR / "callme_offers.json"

BASE_URL = "https://www.callme.dk"

# maps listing URL -> (product type, allowed productCategory values from the API, use_dynamic_type)
# use_dynamic_type=True means determine product_type from API productCategory instead of URL assignment
CATEGORY_URLS = {
    f"{BASE_URL}/webshop/mobiler/": ("phone", {"handset"}, False),
    f"{BASE_URL}/webshop/tablets/": ("tablet", {"tablet"}, False),
    f"{BASE_URL}/webshop/tilbehoer/kategori/tilbehor-med-abonnement/?ProductBrand=Sony%3BM_rke_143959": ("gaming", {"handset"}, False),
    f"{BASE_URL}/webshop/tilbehoer/kategori/gaming/PlayStation-5/": ("gaming", {"handset", "accessory"}, False),
    f"{BASE_URL}/webshop/tilbehoer/kategori/tilbehor-med-abonnement/": ("accessory", {"handset", "accessory", ""}, True),
}

CONSENT_COOKIES: list["SetCookieParam"] = [
    {"name": "CookieInformationConsent", "value": "true", "domain": ".callme.dk", "path": "/"},
]


def get_product_type_from_api_category(api_category, product_name=""):
    # CallMe's API has a very inconsistent productCategory field
    if api_category == "handset":
        # Could be a phone, gaming console, or smartwatch
        name_lower = product_name.lower()
        if any(x in name_lower for x in ["playstation", "ps5", "xbox", "nintendo", "switch"]):
            return "gaming"
        elif any(x in name_lower for x in ["watch", "smartwatch", "galaxy watch"]):
            return "smartwatch"
        elif any(x in name_lower for x in ["ipad", "tablet"]):
            return "tablet"
        else:
            return "phone"
    elif api_category == "accessory":
        # Audio, wearables, gaming accessories, etc.
        name_lower = product_name.lower()
        if any(x in name_lower for x in ["airpods", "earbuds", "headphones", "headset", "speaker", "beats", "boombox", "sonos", "jbl", "harman", "beyerdynamic"]):
            return "audio"
        elif any(x in name_lower for x in ["watch", "smartwatch", "galaxy watch", "pixel watch"]):
            return "smartwatch"
        elif any(x in name_lower for x in ["backbone", "controller", "gamepad"]):
            return "gaming"
        else:
            return "accessory"
    elif api_category == "tablet":
        return "tablet"
    elif api_category == "":
        # Blank category - likely tablets or larger devices
        name_lower = product_name.lower()
        if "ipad" in name_lower:
            return "tablet"
        elif any(x in name_lower for x in ["watch", "smartwatch"]):
            return "smartwatch"
        return "accessory"
    else:
        return "accessory"


def download_image(image_url, product_name):
    if not image_url or not product_name:
        return ""

    if image_url.startswith("/"):
        image_url = f"{BASE_URL}{image_url}"
    elif image_url.startswith("//"):
        image_url = f"https:{image_url}"

    filename = re.sub(r"[^a-z0-9]", "_", product_name.lower()) + ".webp"
    save_path = CALLME_IMAGE_DIR / filename
    CALLME_IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    if save_path.exists():
        return f"/images/callme/{filename}"

    try:
        response = requests.get(image_url, timeout=15)
        if response.status_code == 200:
            with save_path.open("wb") as f:
                f.write(response.content)
            return f"/images/callme/{filename}"
    except Exception as e:
        print(f"  Couldn't download image for '{product_name}': {e}")

    return ""


def parse_price(text):
    # extract integer price from a string like '4.499 kr.' or '3.199 kr.'
    if not text:
        return None
    cleaned = re.sub(r"\D", "", text.replace(".", ""))
    try:
        return int(cleaned)
    except ValueError:
        return None


def parse_min_cost(minimum_price_text):
    # extract the mindstepris number, e.g. 5.022 from "Mindstepris 5.022 kr. med ..."
    if not minimum_price_text:
        return None
    m = re.search(r"Mindstepris\s+([\d.]+)\s*kr", minimum_price_text, re.IGNORECASE)
    if m:
        return int(m.group(1).replace(".", ""))
    return None


def parse_monthly_prices(minimum_price_text):
    # extract monthly prices from the mindstepris string
    # e.g. "... 79 kr. i 6 mdr. herefter 129 kr." -> (79, 6, 129)
    if not minimum_price_text:
        return None, None, None

    m = re.search(
        r"([\d.]+)\s*kr\.?\s+i\s+(\d+)\s+mdr?\.?\s+herefter\s+([\d.]+)\s*kr",
        minimum_price_text,
        re.IGNORECASE,
    )
    if m:
        return int(m.group(1).replace(".", "")), int(m.group(2)), int(m.group(3).replace(".", ""))

    # simpler fallback: just "X kr./md"
    m2 = re.search(r"([\d.]+)\s*kr\.?/md", minimum_price_text, re.IGNORECASE)
    if m2:
        return int(m2.group(1).replace(".", "")), None, None

    return None, None, None


def normalize_product_name(name):
    # Keep product title up to the storage token and drop color/other trailing descriptors.
    if not name:
        return ""

    normalized = re.sub(
        r"^(.*?\b\d+\s?(?:gb|tb)\b).*$",
        r"\1",
        name,
        flags=re.IGNORECASE,
    ).strip()
    return normalized or name.strip()


def build_entry(hit, product_type, date_time, use_api_category=False):
    # build a single offer entry from one API hit, using only the first (default) color variant
    # if use_api_category=True, determine product_type from the API's productCategory and product name
    
    if use_api_category:
        product_name = hit.get("productName", "")
        api_category = hit.get("productCategory", "")
        product_type = get_product_type_from_api_category(api_category, product_name)
    
    base_product_url = hit.get("productUrl", "")
    full_price_text = hit.get("fullPrice", "")
    minimum_price_text = hit.get("minimumPrice", "")

    price_with_subscription = parse_price(full_price_text)
    min_cost_6_months = parse_min_cost(minimum_price_text)
    promo_price, promo_months, regular_price = parse_monthly_prices(minimum_price_text)

    subscription_price_monthly = promo_price or regular_price

    # recalculate min cost from parts if not directly available
    if not min_cost_6_months and price_with_subscription and subscription_price_monthly:
        if promo_months is not None and regular_price is not None:
            remaining = max(0, 6 - promo_months)
            min_cost_6_months = price_with_subscription + promo_months * promo_price + remaining * regular_price
        else:
            min_cost_6_months = price_with_subscription + 6 * subscription_price_monthly

    # use only the first color's default variant
    first_color = hit.get("availableColors", [{}])[0]
    default_img = first_color.get("defaultImage", "")
    variant = next((v for v in first_color.get("variants", []) if v.get("isDefaultVariant")), None)

    if not variant:
        return None

    variant_name = variant.get("name", hit.get("productName", ""))
    variant_name = normalize_product_name(variant_name)

    badge = variant.get("badgeText") or {}
    sold_out = "true" if "udsolgt" in (badge.get("item2", "")).lower() else "false"

    product_link = f"{BASE_URL}{base_product_url}" if base_product_url.startswith("/") else base_product_url

    img_url = default_img + "?width=400" if default_img and "?" not in default_img else default_img
    local_image = download_image(img_url, variant_name)

    return {
        "link": product_link,
        "product_name": variant_name,
        "image_url": local_image,
        "provider": "CallMe",
        "type": product_type,
        "signup_price": 0,
        "data_gb": 0,
        "price_without_subscription": 0,
        "price_with_subscription": price_with_subscription,
        "subscription_price_monthly": subscription_price_monthly,
        "subscription_price_monthly_after_promo": regular_price,
        "min_cost_6_months": min_cost_6_months,
        "discount_on_product": 0,
        "saved_at": date_time,
        "sold_out": sold_out,
    }


def scrape_callme():
    CALLME_DATA_DIR.mkdir(parents=True, exist_ok=True)
    CALLME_IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    date_time = datetime.datetime.now().strftime("%d-%m-%Y-%H:%M")
    all_entries = []
    seen_names = set()

    is_ci = os.environ.get("CI") == "true"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=is_ci)
        context = browser.new_context(
            locale="da-DK",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        context.add_cookies(CONSENT_COOKIES)
        page = context.new_page()

        for cat_url, (product_type, allowed_categories, use_dynamic_type) in CATEGORY_URLS.items():
            print(f"\nScraping: {cat_url} (type={product_type})")

            # collect all hits from every catalog/search API call fired by this page
            all_hits = []

            def handle_response(resp):
                if "catalog/search" in resp.url:
                    try:
                        data = resp.json()
                        all_hits.extend(data.get("hits", []))
                    except Exception:
                        pass

            page.on("response", handle_response)

            try:
                page.goto(cat_url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(2000)
            except Exception as e:
                print(f"  [WARN] Could not load {cat_url}: {e}")
                page.remove_listener("response", handle_response)
                continue

            page.remove_listener("response", handle_response)

            # filter out accessories that are recommended alongside the main products
            hits = [h for h in all_hits if h.get("productCategory") in allowed_categories]
            print(f"  {len(hits)} relevant hits (out of {len(all_hits)} total)")

            for hit in hits:
                entry = build_entry(hit, product_type, date_time, use_api_category=use_dynamic_type)
                if not entry:
                    continue
                name = entry["product_name"]
                if name and name not in seen_names and "brugt" not in name.lower():
                    seen_names.add(name)
                    all_entries.append(entry)
                    print(f"  {name}: pris={entry['price_with_subscription']} kr, min6={entry['min_cost_6_months']} kr, udsolgt={entry['sold_out']}")

        browser.close()

    with CALLME_OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(all_entries, f, ensure_ascii=False, indent=4)

    print(f"\nDone. Saved {len(all_entries)} offers to '{CALLME_OUTPUT_FILE}'")


if __name__ == "__main__":
    scrape_callme()

