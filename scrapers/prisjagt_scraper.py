import json
import os
import re
import datetime
import random
import time
from difflib import SequenceMatcher
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

is_ci = os.environ.get('CI') == 'true'  # GitHub Actions sets this automatically


def clean_search_query(product_name):
    # remove color in parentheses e.g. "(obsidian)", "(sort)"
    name = re.sub(r'\(.*?\)', '', product_name)
    # remove generic words that hurt search results
    name = re.sub(r'\bsmartphone\b|\bLTE\b', '', name, flags=re.IGNORECASE)
    # clean up extra whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def normalize(text):
    # clean up for better matching: lowercase, remove punctuation, collapse whitespace
    text = text.lower()
    # convert + to 'plus' before stripping punctuation so "S25+" becomes "s25 plus"
    # and gets correctly caught by the tier word check
    text = re.sub(r'\+', ' plus ', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# if a product has one of these in the name, but the query doesn't, it's almost certainly a different product tier (eg "Pro" vs "Pro Max")
TIER_WORDS = {'ultra', 'plus', 'pro', 'max', 'mini', 'fe', 'fold', 'flip', 'lite', 'edge', 'air'}


def extract_storage(text):
    """Extract storage size in GB as an integer, or None if not specified.
    Skips RAM mentions like '12GB RAM' or '8GB RAM' — we only want the storage figure.
    Handles '128GB', '256 GB', '1TB' (converted to 1024GB) etc."""
    # remove RAM mentions first so "12GB RAM 256GB" doesn't return 12
    cleaned = re.sub(r'\d+\s*GB\s*RAM', '', text, flags=re.IGNORECASE)
    # now match TB
    m = re.search(r'(\d+)\s*TB', cleaned, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 1024
    # then GB
    m = re.search(r'(\d+)\s*GB', cleaned, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def split_fused_tokens(text):
    """
    Split fused alpha+digit tokens so tier word checks work even when Prisjagt
    writes 'Flip7' instead of 'Flip 7'. E.g. 'flip7' -> {'flip', '7', 'flip7'}.
    """
    text = normalize(text)
    tokens = set()
    for word in text.split():
        parts = re.findall(r'[a-z]+|\d+', word)
        tokens.update(parts)
        tokens.add(word)
    return tokens


def extract_model_number(text):
    """
    Extract the primary model number string for exact-match comparison.
    Returns a normalised string like '16e', 'a36', 's25', '17' etc., or None.
    We look for the first alpha-numeric or standalone numeric token that follows
    a known brand/series keyword and looks like a model identifier (not RAM/storage).
    Strategy: strip storage/RAM, strip brand words, return first meaningful token.
    """
    # remove storage and RAM so they don't confuse things
    text = re.sub(r'\d+\s*GB\s*RAM', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\d+\s*(GB|TB)', '', text, flags=re.IGNORECASE)
    # remove known non-model words
    noise = {'samsung', 'apple', 'google', 'motorola', 'oneplus', 'nothing', 'urbanista',
             'galaxy', 'iphone', 'pixel', 'moto', 'nord', 'razr', 'leva',
             '5g', '4g', 'lte', 'dual', 'sim', 'sm', 'smartphone', 'wireless',
             'black', 'white', 'blue', 'green', 'grey', 'gray', 'silver', 'gold',
             'sort', 'grå', 'hvid', 'obsidian', 'coral', 'red', 'jetblack',
             'dark', 'true', 'on', 'ear', 'tws', 'gen'}
    noise.update(TIER_WORDS)
    tokens = normalize(text).split()
    for token in tokens:
        if token in noise:
            continue
        # must contain at least one digit to be a model number
        if re.search(r'\d', token):
            return token
    return None


def score_match(query, candidate):
    # returns a float 0–1. Higher = better match.

    q_tokens = split_fused_tokens(query)
    c_tokens = split_fused_tokens(candidate)

    for word in TIER_WORDS:
        # disqualify if candidate has a tier word the query doesn't
        if word in c_tokens and word not in q_tokens:
            return 0.0
        # disqualify if query has a tier word the candidate is missing
        if word in q_tokens and word not in c_tokens:
            return 0.0

    # disqualification: query specifies a storage size and candidate has a different one
    q_storage = extract_storage(query)
    c_storage = extract_storage(candidate)
    if q_storage is not None and c_storage is not None and q_storage != c_storage:
        return 0.0

    # disqualification: model number mismatch — e.g. "iPhone 16" vs "iPhone 16e", "A36" vs "S25"
    q_model = extract_model_number(query)
    c_model = extract_model_number(candidate)
    if q_model and c_model and q_model != c_model:
        q_parts = set(re.findall(r'[a-z]+|\d+', q_model))
        c_parts = set(re.findall(r'[a-z]+|\d+', c_model))

        q_digits = {p for p in q_parts if p.isdigit()}
        c_digits = {p for p in c_parts if p.isdigit()}

        q_alpha = q_parts - q_digits
        c_alpha = c_parts - c_digits
        if q_digits == c_digits and q_alpha == c_alpha:
            pass  # identical in all parts — same model
        elif q_digits == c_digits and (not q_alpha or not c_alpha):
            # one side is purely numeric ('7'), other is fused ('flip7')
            # only safe if the extra alpha part is a known series word (flip/fold/etc)
            # which is already checked by TIER_WORDS above — so allow it
            extra_alpha = q_alpha or c_alpha
            if extra_alpha.issubset(TIER_WORDS):
                pass  # e.g. '7' matches 'flip7' because 'flip' is a tier word
            else:
                return 0.0  # e.g. '16' must NOT match '16e' ('e' is not a tier word)
        else:
            return 0.0

    return SequenceMatcher(None, normalize(query), normalize(candidate)).ratio()


def get_card_title(card):
    # strategy 1: standard heading tags
    for sel in ['h2', 'h3', 'h4']:
        el = card.query_selector(sel)
        if el:
            t = el.inner_text().strip()
            if t:
                return t

    # strategy 2: class-name hints
    for sel in ['[class*="title"]', '[class*="name"]', '[class*="heading"]', '[class*="product"]']:
        el = card.query_selector(sel)
        if el:
            t = el.inner_text().strip()
            if t and len(t) > 3:
                return t

    # strategy 3: aria-label or title attribute on the wrapping <a> tag
    el = card.query_selector('a')
    if el:
        for attr in ['aria-label', 'title']:
            v = el.get_attribute(attr)
            if v and len(v) > 3:
                return v.strip()

    # strategy 4: take the longest non-price line from all card text
    lines = [l.strip() for l in card.inner_text().splitlines() if l.strip()]
    non_price = [l for l in lines if not re.match(r'^[\d.,\s]+kr', l)]
    if non_price:
        return max(non_price, key=len)

    return ""


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
        print(f"  -> Could not load results for: {product_name}")
        return None, False

    cards = page.query_selector_all('[data-test="ProductGridCard"]')
    if not cards:
        print(f"  -> No cards found on page")
        return None, True


    # collect (title, price_element) for every card
    candidates = []
    for card in cards:
        title = get_card_title(card)

        price_el = card.query_selector(
            '[data-sentry-element="Component"][data-sentry-component="Text"].font-heaviest'
        )
        if not price_el:
            for el in card.query_selector_all('[data-sentry-component="Text"]'):
                if 'kr' in el.inner_text():
                    price_el = el
                    break

        if title and price_el:
            candidates.append((title, price_el))
        else:
            if not candidates:
                return None, True

    query_clean = clean_search_query(product_name)
    q_has_storage = extract_storage(query_clean) is not None

    # score every candidate and discard disqualified ones
    scored = [(score_match(query_clean, title), title, price_el) for title, price_el in candidates]
    scored = [s for s in scored if s[0] > 0.0]

    if not scored:
        print(f"  -> All results are wrong product tier, skipping")
        return None, True

    best_score = scored[0][0]

    if best_score < 0.2:
        return None, True

    # keep only candidates within 5% of the best score (essentially tied)
    top_candidates = [(score, title, price_el) for score, title, price_el in scored
                      if score >= best_score * 0.95]

    if q_has_storage:
        # storage already handled by disqualification — just take the best scorer
        best_score, best_title, best_price_el = top_candidates[0]
    else:
        # no storage in query: among tied top candidates, prefer the lowest storage size
        # (subscription sites typically sell entry-level storage)
        def storage_sort_key(item):
            s = extract_storage(item[1])
            return s if s is not None else 9999  # no storage info goes last

        top_candidates.sort(key=storage_sort_key)
        best_score, best_title, best_price_el = top_candidates[0]


    raw = best_price_el.inner_text().strip()
    digits = "".join(re.findall(r'\d+', raw))
    return (int(digits) if digits else None), True


def make_fresh_page(browser):

    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
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
    os.makedirs(os.path.join(BASE_DIR, 'data/prisjagt'), exist_ok=True)

    providers = [
        ('data/telmore/telmore_offers.json', 'product_name'),
        ('data/oister/oister_offers.json', 'product_name'),
        ('data/elgiganten/elgiganten_offers.json', 'product'),
        ('data/cbb/cbb_offers.json', 'product_name'),
        ('data/3/3_offers.json', 'product_name'),
        ('data/yousee/yousee_offers.json', 'product_name'),
        ('data/norlys/norlys_offers.json', 'product_name'),
    ]

    products = []
    for path, name_field in providers:
        full_path = os.path.join(BASE_DIR, path)
        if os.path.exists(full_path):
            with open(full_path, encoding='utf-8') as f:
                offers = json.load(f)
            for offer in offers:
                name = offer.get(name_field, '')
                if name:
                    products.append(name)

    products = list(set(products))

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
            print(f"Looking up: {product_name}")
            price, page_loaded = get_market_price(page, product_name)

            if not page_loaded:
                consecutive_failures += 1
                print(f"  [failure {consecutive_failures}/{failure_threshold}]")

                if consecutive_failures >= failure_threshold:
                    print(f"\n  !! {failure_threshold} consecutive failures — recycling browser context and pausing 10s...\n")
                    context.close()
                    time.sleep(10)
                    context, page = make_fresh_page(browser)
                    consecutive_failures = 0

                    print(f"  Retrying: {product_name}")
                    price, page_loaded = get_market_price(page, product_name)
            else:
                consecutive_failures = 0

            results[product_name] = {
                "market_price": price,
                "looked_up_at": date_time
            }
            print(f"  -> {price} kr.")

        context.close()
        browser.close()

    with open(os.path.join(BASE_DIR, 'data/prisjagt/prisjagt_prices.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=4)

    print(f"\nLooked up {len(results)} products.")


if __name__ == "__main__":
    scrape_prisjagt()