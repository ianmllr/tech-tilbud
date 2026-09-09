import json
import os
import re
import datetime
import random
import time
from pathlib import Path
from difflib import SequenceMatcher
from playwright.sync_api import ViewportSize, sync_playwright
from playwright_stealth import Stealth
from provider_sources import PROVIDER_SOURCES
from scraper_utils import log, is_blacklisted

BASE_DIR = Path(__file__).resolve().parent.parent
VIEWPORT: ViewportSize = {"width": 1920, "height": 1080}

is_ci = os.environ.get('CI') == 'true'


def clean_search_query(product_name):
    # remove color in parentheses e.g. "(obsidian)", "(sort)"
    name = re.sub(r'\(.*?\)', '', product_name)
    # remove generic words that hurt search results
    name = re.sub(r'\bsmartphone\b|\bLTE\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def normalize(text):
    # lowercase, convert "+" to "plus", strip punctuation, collapse whitespace
    text = text.lower()
    text = re.sub(r'\+', ' plus ', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# tier words — if a candidate has one the query doesn't (or vice versa), it's a different product
TIER_WORDS = {'ultra', 'aktiv støjreduktion', 'anc', 'plus', 'pro', 'max', 'mini', 'fe', 'fold', 'flip', 'lite', 'edge', 'air'}

# accessory keywords — disqualify any candidate that is clearly not a device
ACCESSORY_KEYWORDS = {
    'case', 'cover', 'etui', 'skærmbeskyttelse', 'screen protector', 'beskyttelsesglas',
    'oplader', 'charger', 'kabel', 'cable', 'rem', 'strap', 'sleeve',
    'folie', 'glass', 'bumper', 'wallet', 'pung', 'holder', 'stand', 'dock',
    'batteri', 'battery', 'ear', 'stylus', 'pen',
    'loop', 'band', 'trail loop', 'alpine loop', 'milanese', 'sport loop',
}


def extract_storage(text):
    # returns storage in GB as an int, or None
    # skips RAM mentions like "12GB RAM" so only the storage figure is returned
    cleaned = re.sub(r'\d+\s*GB\s*RAM', '', text, flags=re.IGNORECASE)
    m = re.search(r'(\d+)\s*TB', cleaned, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 1024
    m = re.search(r'(\d+)\s*GB', cleaned, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def split_fused_tokens(text):
    # split fused alpha+digit tokens so tier word checks work even when Prisjagt
    # writes "Flip7" instead of "Flip 7" — e.g. "flip7" -> {"flip", "7", "flip7"}
    text = normalize(text)
    tokens = set()
    for word in text.split():
        parts = re.findall(r'[a-z]+|\d+', word)
        tokens.update(parts)
        tokens.add(word)
    return tokens


def extract_model_number(text: str) -> str | None:
    # extract the primary model number for exact-match comparison e.g. "16e", "a36", "s25"
    text = re.sub(r'\d+\s*GB\s*RAM', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\d+\s*(GB|TB)', '', text, flags=re.IGNORECASE)
    noise = {'samsung', 'apple', 'google', 'motorola', 'oneplus', 'nothing', 'urbanista',
             'galaxy', 'iphone', 'pixel', 'moto', 'nord', 'razr', 'leva',
             '5g', '4g', 'lte', 'dual', 'sim', 'sm', 'smartphone', 'wireless',
             'black', 'white', 'blue', 'green', 'grey', 'gray', 'silver', 'gold',
             'sort', 'grå', 'hvid', 'obsidian', 'coral', 'red', 'jetblack',
             'dark', 'true', 'on', 'ear', 'tws', 'gen', 'silver shadow',
             'space', 'cosmic', 'ocean', 'starlight', 'midnight', 'sunrise',
             'space grey', 'grisaille', 'charcoal grey', 'navy', 'silhouette',
             'moonstone', 'graphite', 'obsidian', 'blueblack', }
    noise.update(TIER_WORDS)
    tokens = normalize(text).split()
    for token in tokens:
        if token in noise:
            continue
        # must contain at least one digit to qualify as a model number
        if re.search(r'\d', token):
            return token
    return None


def score_match(query, candidate):
    # returns a float 0–1, higher = better match

    # disqualify accessories — cases, covers, cables, bands, etc.
    candidate_lower = candidate.lower()
    if any(kw in candidate_lower for kw in ACCESSORY_KEYWORDS):
        return 0.0

    q_tokens = split_fused_tokens(query)
    c_tokens = split_fused_tokens(candidate)

    # disqualify if either side has a tier word the other is missing
    for word in TIER_WORDS:
        if word in c_tokens and word not in q_tokens:
            return 0.0
        if word in q_tokens and word not in c_tokens:
            return 0.0

    # disqualify if both sides specify storage but it differs
    q_storage = extract_storage(query)
    c_storage = extract_storage(candidate)
    if q_storage is not None and c_storage is not None and q_storage != c_storage:
        return 0.0

    # disqualify if model numbers differ e.g. "iPhone 16" vs "iPhone 16e"
    q_model = extract_model_number(query)
    c_model = extract_model_number(candidate)
    if q_model is not None and c_model is not None and q_model != c_model:
        q_parts = set(re.findall(r"[a-z]+|\d+", q_model))
        c_parts = set(re.findall(r"[a-z]+|\d+", c_model))
        q_digits = {p for p in q_parts if p.isdigit()}
        c_digits = {p for p in c_parts if p.isdigit()}
        q_alpha = q_parts - q_digits
        c_alpha = c_parts - c_digits
        if q_digits == c_digits and q_alpha == c_alpha:
            pass
        elif q_digits == c_digits and (not q_alpha or not c_alpha):
            extra_alpha = q_alpha or c_alpha
            if extra_alpha.issubset(TIER_WORDS):
                pass
            else:
                return 0.0
        else:
            return 0.0

    return SequenceMatcher(None, normalize(query), normalize(candidate)).ratio()


def get_market_price(page, product_name):

    query = clean_search_query(product_name).replace(' ', '+')
    url = (
        f"https://prisjagt.dk/search?availability=AVAILABLE&query={query}"
        f"&category=pc%3Amobiltelefoner%7Cpc%3Asmartwatches%7Cpc%3Ahovedtelefoner%7Cpc%3Atablets&sort=score"
    )

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(random.uniform(1500, 3000))
        page.wait_for_selector('[data-test="ProductGridCard"]', timeout=8000)
    except:
        log(f"  -> Could not load results for: {product_name}")
        return None, False

    cards = page.query_selector_all('[data-test="ProductGridCard"]')
    if not cards:
        return None, True

    # collect (title, price_element) for every card that has both
    candidates = []
    for card in cards:
        title_el = card.query_selector('[class*="product"]')
        title = title_el.inner_text().strip() if title_el else ""

        price_el = card.query_selector(
            '[data-sentry-element="Component"][data-sentry-component="Text"].font-heaviest'
        )

        if title and price_el:
            candidates.append((title, price_el))

    if not candidates:
        return None, True

    query_clean = clean_search_query(product_name)
    q_has_storage = extract_storage(query_clean) is not None

    # score and sort candidates — highest score first
    scored = [(score_match(query_clean, title), title, price_el) for title, price_el in candidates]
    scored = [s for s in scored if s[0] > 0.0]

    if not scored:
        log(f"  -> All candidates disqualified")
        return None, True

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score = scored[0][0]

    if best_score < 0.4:
        log(f"  -> Best score {best_score:.2f} below threshold, skipping")
        return None, True

    # keep candidates within 15% of the best score — wide enough for storage variants to all be included
    top_candidates = [s for s in scored if s[0] >= best_score * 0.85]

    if q_has_storage:
        best_score, best_title, best_price_el = top_candidates[0]
    else:
        # no storage in query — among tied candidates, prefer the smallest storage size
        def storage_sort_key(item):
            s = extract_storage(item[1])
            return s if s is not None else 9999

        top_candidates.sort(key=storage_sort_key)
        best_score, best_title, best_price_el = top_candidates[0]

    log(f"  -> Matched: '{best_title}' (score={best_score:.2f})")

    # get number as int instead of danihs number (eg 4.299 -> 4299)
    raw = best_price_el.inner_text().strip()
    price_clean = re.sub(r'\.(?=\d{3}(\D|$))', '', raw)
    price_clean = re.sub(r',\d+', '', price_clean)
    digits = "".join(re.findall(r'\d+', price_clean))
    return (int(digits) if digits else None), True


def make_fresh_page(browser):

    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport=VIEWPORT,
        locale="da-DK",
        timezone_id="Europe/Copenhagen",
        color_scheme="light",
        java_script_enabled=True,
        has_touch=False,
        is_mobile=False,
    )
    context.add_cookies([
        {"name": "consentDate",  "value": "2026-02-23T17:25:15.142Z",                "domain": "prisjagt.dk", "path": "/"},
        {"name": "consentUUID", "value": "b7d4dfb8-a27d-43a9-bca2-4b1dbb3205ff_53", "domain": "prisjagt.dk", "path": "/"},
    ])
    page = context.new_page()
    Stealth().use_sync(page)
    page.goto("https://prisjagt.dk", wait_until="domcontentloaded")
    return context, page


def scrape_prisjagt():
    (BASE_DIR / 'data' / 'prisjagt').mkdir(parents=True, exist_ok=True)

    products = []
    for path, name_field in PROVIDER_SOURCES:
        full_path = BASE_DIR / path
        if full_path.exists():
            with full_path.open(encoding='utf-8') as f:
                offers = json.load(f)
            for offer in offers:
                name = offer.get(name_field, '')
                if not name and name_field == 'product_name':
                    name = offer.get('product', '')
                if name:
                    products.append(name)

    products = list(set(products))

    # drop globally blacklisted products so we never spend a lookup on them
    blacklisted = [name for name in products if is_blacklisted(name)]
    if blacklisted:
        log(f"Skipping {len(blacklisted)} blacklisted product(s)")
    products = [name for name in products if not is_blacklisted(name)]

    results = {}
    date_time = datetime.datetime.now().strftime("%d-%m-%Y-%H:%M")

    failure_threshold = 3

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=is_ci,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        context, page = make_fresh_page(browser)
        consecutive_failures = 0

        for product_name in products:
            log(f"Looking up: {product_name}")
            price, page_loaded = get_market_price(page, product_name)

            if not page_loaded:
                consecutive_failures += 1
                log(f"  [failure {consecutive_failures}/{failure_threshold}]")

                if consecutive_failures >= failure_threshold:
                    log(f"\n  !! {failure_threshold} consecutive failures — recycling browser context and pausing 10s...\n")
                    context.close()
                    time.sleep(10)
                    context, page = make_fresh_page(browser)
                    consecutive_failures = 0

                    log(f"  Retrying: {product_name}")
                    price, page_loaded = get_market_price(page, product_name)
            else:
                consecutive_failures = 0

            results[product_name] = {
                "market_price": price,
                "looked_up_at": date_time
            }
            log(f"  -> {price} kr.")

        context.close()
        browser.close()

    with (BASE_DIR / 'data' / 'prisjagt' / 'prisjagt_prices.json').open('w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=4)

    log(f"\nLooked up {len(results)} products.")


if __name__ == "__main__":
    scrape_prisjagt()