import json
import os
import re
import datetime
import random
import time
from difflib import SequenceMatcher
from pathlib import Path
from playwright.sync_api import ViewportSize, sync_playwright
from playwright_stealth import Stealth
from provider_sources import PROVIDER_SOURCES
from scraper_utils import log, apply_name_substitutions, is_blacklisted

# setup
BASE_DIR = Path(__file__).resolve().parent.parent
VIEWPORT: ViewportSize = {"width": 1920, "height": 1080}

is_ci = os.environ.get('CI') == 'true'


def clean_search_query(product_name):
    # remove color in parentheses e.g. "(obsidian)", "(sort)"
    name = re.sub(r'\(.*?\)', '', product_name)
    # remove subscription suffix used by some providers
    name = re.sub(r'\bmed\s+abonnement\b', '', name, flags=re.IGNORECASE)
    # remove generic words that hurt search results
    name = re.sub(r'\bsmartphone\b|\bLTE\b', '', name, flags=re.IGNORECASE)
    # normalize separators left after removals
    name = re.sub(r'\s+-\s+', ' - ', name)
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
TIER_WORDS = {'ultra', 'cellular', 'aktiv støjreduktion', 'anc', 'plus', 'pro', 'max', 'mini', 'fe', 'fold', 'flip', 'lite', 'edge', 'air'}

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
    # split fused alpha+digit tokens so tier word checks work even when PriceRunner
    # writes "Flip7" instead of "Flip 7" — e.g. "flip7" -> {"flip", "7", "flip7"}
    text = normalize(text)
    tokens = set()
    for word in text.split():
        parts = re.findall(r'[a-z]+|\d+', word)
        tokens.update(parts)
        tokens.add(word)
    return tokens


def extract_model_number(text):
    # extract the primary model number for exact-match comparison e.g. "16e", "a36", "s25"
    text = re.sub(r'\d+\s*GB\s*RAM', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\d+\s*(GB|TB)', '', text, flags=re.IGNORECASE)
    noise = {'samsung', 'apple', 'google', 'motorola', 'oneplus', 'nothing', 'urbanista',
             'galaxy', 'iphone', 'pixel', 'moto', 'nord', 'razr', 'leva',
             '5g', '4g', 'lte', 'dual', 'sim', 'sm', 'smartphone', 'wireless',
             'black', 'white', 'blue', 'green', 'grey', 'gray', 'silver', 'gold',
             'sort', 'grå', 'hvid', 'obsidian', 'coral', 'red', 'jetblack',
             'dark', 'true', 'on', 'ear', 'tws', 'gen', 'space black', 'wifi',
             # tablet screen sizes — not model numbers
             '10', '11', '12', '13', '14', '15', '16', '17', '18', '20', '24', '27'}
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
    if q_model and c_model and q_model != c_model:
        q_parts = set(re.findall(r'[a-z]+|\d+', q_model))
        c_parts = set(re.findall(r'[a-z]+|\d+', c_model))
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

    # disqualify if the candidate has extra bare numeric tokens the query doesn't have
    # e.g. "Motorola Edge 60 12 512GB" has a bare "12" (unlabelled RAM) absent from the query
    def _non_storage_digits(text, storage, model):
        storage_str = str(storage) if storage else None
        model_digits = set(re.findall(r'\d+', model)) if model else set()
        result = set()
        for tok in normalize(text).split():
            if not tok.isdigit():
                continue
            if storage_str and tok == storage_str:
                continue
            if tok in model_digits:
                continue
            result.add(tok)
        return result

    q_extra_digits = _non_storage_digits(query, q_storage, q_model)
    c_extra_digits = _non_storage_digits(candidate, c_storage, c_model)
    if c_extra_digits - q_extra_digits:
        return 0.0

    return SequenceMatcher(None, normalize(query), normalize(candidate)).ratio()


def parse_price_text(price_text):
    # convert e.g. "10.899,00 kr." -> 10899
    if not price_text:
        return None
    lowered = price_text.lower()
    # ignore recurring-payment / financing strings so we don't treat monthly price
    # values (e.g. "2.783 kr./md.") as the market price.
    if any(marker in lowered for marker in [
        '/md', 'kr/md', 'kr. md', 'pr. md', 'pr md', 'pr. måned', 'pr måned',
        'måned', 'måneds', 'betalinger af', 'afdrag', 'herefter', 'derefter', 'mdr',
        'brugt', 'brugte', 'used', 'fragt', 'levering', 'shipping',
    ]):
        return None
    # Extract standalone price-like tokens ending in kr.
    matches = re.findall(r'(\d{1,3}(?:\.\d{3})+|\d{4,})\s*kr\.?', price_text, flags=re.IGNORECASE)
    if not matches:
        return None
    prices = [int(m.replace('.', '')) for m in matches]
    # If the snippet contains multiple price numbers without an explicit context,
    # treat it as ambiguous and skip it.
    if len(prices) > 1:
        if 'laveste pris' in lowered or 'sammenlign priser fra' in lowered:
            return prices[0]
        return None
    return prices[0]


def extract_laveste_pris(page) -> int | None:
    # Prefer the explicit "Laveste pris" section on the detail page.
    try:
        main = page.locator("main").first
        if main.count():
            text = main.inner_text() or ""
        else:
            text = page.inner_text("body")
    except Exception:
        return None

    hits = re.findall(r'Laveste pris[\s\S]{0,140}?(\d{1,3}(?:\.\d{3})+|\d{4,})\s*kr', text, flags=re.IGNORECASE)
    if not hits:
        return None
    return int(hits[0].replace('.', ''))


def extract_offer_row_prices(page) -> list[int]:
    # Fallback: collect one-time product prices from offer rows only, excluding
    # used, shipping and installment contexts.
    try:
        prices = page.evaluate("""() => {
            const root = document.querySelector('main') || document.body;
            const nodes = root.querySelectorAll('span, strong, p, div, a');
            const out = [];
            const priceRe = /^\s*\d{1,3}(?:\.\d{3})*\s*kr\.?\s*$/i;
            const banned = ['brugt', 'brugte', 'used', 'fragt', 'levering', 'shipping', '/md', 'pr. md', 'pr md', 'måned', 'måneds', 'betalinger af', 'afdrag'];
            for (const n of nodes) {
                const t = (n.innerText || n.textContent || '').trim();
                if (!priceRe.test(t)) continue;
                let ctx = '';
                let p = n;
                for (let i = 0; i < 3 && p; i++) {
                    ctx += ' ' + ((p.innerText || p.textContent || '').toLowerCase());
                    p = p.parentElement;
                }
                if (banned.some(b => ctx.includes(b))) continue;
                out.push(t);
            }
            return Array.from(new Set(out));
        }""")
    except Exception:
        return []

    parsed: list[int] = []
    for t in prices:
        p = parse_price_text(t)
        if p is not None:
            parsed.append(p)
    return parsed


def get_market_price(page, product_name):

    query = clean_search_query(product_name).replace(' ', '+')
    url = f"https://www.pricerunner.dk/results?q={query}&suggestionsActive=true&suggestionClicked=false&suggestionReverted=false"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(random.uniform(2000, 3500))
    except Exception:
        log(f"Could not load page for: {product_name}")
        return None, False

    # each product card is an <a> with a title attribute and href starting with "/pl/"
    card_links = page.query_selector_all('a[href^="/pl/"][title]')

    if not card_links:
        log(f"No product cards found")
        return None, True

    # collect (title, href, price_texts) for every card
    candidates = []
    for link in card_links:
        title = (link.get_attribute('title') or '').strip()
        href = (link.get_attribute('href') or '').strip()
        if not title:
            continue

        # Collect multiple nearby price-like spans (array) instead of a single price.
        price_texts = []
        try:
            price_texts = link.evaluate("""el => {
                let node = el.parentElement;
                const found = [];
                for (let i = 0; i < 6; i++) {
                    if (!node) break;
                    const spans = node.querySelectorAll('span, div, p, li');
                    for (const s of spans) {
                        const t = (s.innerText || s.textContent || '').trim();
                        if (/\\d/.test(t) && t.toLowerCase().includes('kr') && t.length < 60 && !t.startsWith('-')) {
                            found.push(t);
                        }
                    }
                    node = node.parentElement;
                }
                return Array.from(new Set(found));
            }""")
        except Exception:
            pass

        if title:
            candidates.append((title, href, price_texts or []))

    if not candidates:
        log(f"Could not extract any prices")
        return None, True

    query_clean = clean_search_query(product_name)
    q_has_storage = extract_storage(query_clean) is not None

    # score and sort candidates — highest score first
    # candidates: (title, href, price_texts)
    scored = []
    for title, href, price_texts in candidates:
        score = score_match(query_clean, title)
        # choose the lowest price among card-extracted price_texts as candidate price
        parsed_price = None
        for pt in price_texts:
            p = parse_price_text(pt)
            if p is not None and (parsed_price is None or p < parsed_price):
                parsed_price = p
        scored.append((score, title, href, price_texts, parsed_price))
    scored = [s for s in scored if s[0] > 0.0]

    if not scored:
        log(f"All candidates disqualified")
        return None, True

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score = scored[0][0]

    if best_score < 0.4:
        log(f"Best score {best_score:.2f} below threshold, skipping")
        return None, True

    # keep candidates within 15% of the best score — wide enough for storage/colour variants to all be included
    top_candidates = [s for s in scored if s[0] >= best_score * 0.85]

    # For increased accuracy: visit detail pages for top candidates and use structured
    # extraction similar to other scrapers (label-first + scoped fallback).
    N_DETAIL = 3
    detail_price_map = {}
    for score, title, href, price_texts, parsed_price in top_candidates[:N_DETAIL]:
        if not href:
            continue
        try:
            detail_url = f"https://www.pricerunner.dk{href}" if href.startswith('/') else href
            page.goto(detail_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(random.uniform(1000, 2000))

            laveste = extract_laveste_pris(page)
            row_prices = extract_offer_row_prices(page)
            detail_price = laveste if laveste is not None else (min(row_prices) if row_prices else None)
            detail_price_map[href] = {'price': detail_price}
        except Exception:
            # if a detail page fails, ignore and continue with card price
            pass

    if q_has_storage:
        # for exact storage queries, choose the cheapest among top score matches
        priced_top = []
        verified_available = any((detail_price_map.get(href, {}) or {}).get('price') is not None for _, _, href, _, _ in top_candidates)
        for score, title, href, price_texts, parsed_price in top_candidates:
            parsed = (detail_price_map.get(href, {}) or {}).get('price')
            if parsed is None and not verified_available:
                parsed = parsed_price
                if parsed is None and price_texts:
                    parsed_vals = [parse_price_text(pt) for pt in price_texts if parse_price_text(pt) is not None]
                    if parsed_vals:
                        parsed = min(parsed_vals)
            if parsed is not None and (not verified_available or (detail_price_map.get(href, {}) or {}).get('price') is not None):
                priced_top.append((score, title, href, parsed))
        if not priced_top:
            log("No parseable prices among top candidates")
            return None, True
        priced_top.sort(key=lambda x: (x[3], -x[0]))
        best_score, best_title, best_href, best_price = priced_top[0]
    else:
        # no storage in query — among tied candidates, prefer the smallest storage size
        def storage_sort_key(item):
            s = extract_storage(item[1])
            return s if s is not None else 9999


        top_candidates.sort(key=storage_sort_key)
        min_storage = storage_sort_key(top_candidates[0])
        storage_group = [item for item in top_candidates if storage_sort_key(item) == min_storage]
        priced_group = []
        verified_available = any((detail_price_map.get(href, {}) or {}).get('price') is not None for _, _, href, _, _ in storage_group)
        for score, title, href, price_texts, parsed_price in storage_group:
            parsed = (detail_price_map.get(href, {}) or {}).get('price')
            if parsed is None and not verified_available:
                parsed = parsed_price
                if parsed is None and price_texts:
                    parsed_vals = [parse_price_text(pt) for pt in price_texts if parse_price_text(pt) is not None]
                    if parsed_vals:
                        parsed = min(parsed_vals)
            if parsed is not None and (not verified_available or (detail_price_map.get(href, {}) or {}).get('price') is not None):
                priced_group.append((score, title, href, parsed))
        if not priced_group:
            log("No parseable prices in preferred storage group")
            return None, True
        priced_group.sort(key=lambda x: (x[3], -x[0]))
        best_score, best_title, best_href, best_price = priced_group[0]

    log(f"Matched: '{best_title}' (score={best_score:.2f})")

    return best_price, True


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
        {"name": "OptanonAlertBoxClosed", "value": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
         "domain": ".pricerunner.dk", "path": "/"},
        {"name": "OptanonConsent", "value": "isGpcEnabled=0&datestamp=" + datetime.datetime.now().strftime(
            "%a+%b+%d+%Y+%H%%3A%M%%3A%S+GMT%%2B0100") + "&version=202209.1.0&isIABGlobal=false&hosts=&consentId=pricerunner-consent&interactionCount=1&landingPath=NotLandingPage&groups=C0001%%3A1%%2CC0002%%3A1%%2CC0003%%3A1%%2CC0004%%3A1",
         "domain": ".pricerunner.dk", "path": "/"},
    ])
    page = context.new_page()
    Stealth().use_sync(page)
    try:
        page.goto("https://www.pricerunner.dk", wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass  # partial load is fine — we just need cookies set
    page.wait_for_timeout(2000)
    # accept cookie banner if present
    for selector in [
        '#onetrust-accept-btn-handler',
        'button[id*="accept"]',
        'button[class*="accept"]',
        '[data-test="accept-all-cookies"]',
        'button:has-text("Accepter alle")',
        'button:has-text("Acceptér alle")',
    ]:
        try:
            page.click(selector, timeout=3000)
            page.wait_for_timeout(800)
            break
        except Exception:
            pass
    return context, page


def scrape_pricerunner():
    (BASE_DIR / 'data' / 'pricerunner').mkdir(parents=True, exist_ok=True)

    # collect unique product names from all provider files
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
            product_name = apply_name_substitutions(product_name) # back up check for name substitutions
            log(f"Looking up: {product_name}")
            price, page_loaded = get_market_price(page, product_name)

            if not page_loaded:
                consecutive_failures += 1
                log(f"  [failure {consecutive_failures}/{failure_threshold}]")

                if consecutive_failures >= failure_threshold:
                    # recycle the browser context to recover from a potential block
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

    with (BASE_DIR / 'data' / 'pricerunner' / 'pricerunner_prices.json').open('w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=4)

    log(f"\nLooked up {len(results)} products.")


if __name__ == "__main__":
    scrape_pricerunner()

