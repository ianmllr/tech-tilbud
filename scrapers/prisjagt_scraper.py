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
from scraper_utils import log, is_blacklisted, apply_name_substitutions

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
    return name.strip(' -')


def normalize(text):
    # lowercase, convert "+" to "plus", strip punctuation, collapse whitespace
    text = text.lower()
    text = re.sub(r'\+', ' plus ', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# tier words — if a candidate has one the query doesn't (or vice versa), it's a different product
TIER_WORDS = {'ultra', 'aktiv støjreduktion', 'anc', 'plus', 'pro', 'max', 'mini', 'fe', 'fold', 'flip', 'lite', 'edge', 'air',
              # variant qualifiers — "Edge 70" and "Edge 70 Fusion" are different phones
              'fusion', 'power', 'neo', 'xl'}

# accessory keywords — disqualify any candidate that is clearly not a device
ACCESSORY_KEYWORDS = {
    'case', 'cover', 'etui', 'skærmbeskyttelse', 'screen protector', 'beskyttelsesglas',
    'oplader', 'charger', 'kabel', 'cable', 'rem', 'strap', 'sleeve',
    'folie', 'glass', 'bumper', 'wallet', 'pung', 'holder', 'stand', 'dock',
    'batteri', 'battery', 'ear', 'stylus', 'pen',
    'loop', 'band', 'trail loop', 'alpine loop', 'milanese', 'sport loop',
    # danish accessory names — straps and screen protectors outnumber the real
    # watch listings, and a strap price passed as the watch's market price
    'armbånd', 'metalarmbånd', 'urrem', 'rem til', 'skærmbeskytter',
    'beskyttelsescover', 'opladerkabel', 'ladestation', 'taske',
}

ACCESSORY_PATTERN = re.compile(
    r'\b(?:' + '|'.join(re.escape(kw) for kw in sorted(ACCESSORY_KEYWORDS, key=len, reverse=True)) + r')\b',
    re.IGNORECASE,
)


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
        # a release year is not a model number. "iPad Air (2026)" parsed as model
        # "2026" and then failed to match the same product without the year
        if re.fullmatch(r'(19|20)\d{2}', token):
            continue
        # must contain at least one digit to qualify as a model number
        if re.search(r'\d', token):
            return token
    return None


def extract_case_size(text: str) -> str | None:
    # watch case size e.g. "44" from "Galaxy Watch9 44mm". written as a fused
    # token, so the bare-digit check in score_match never sees it
    match = re.search(r"\b(\d{2})\s*mm\b", text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def has_cellular(tokens) -> bool:
    # cellular/lte variant rather than wi-fi or bluetooth only. excludes "5g"/"4g",
    # which appear on nearly every phone listing and carry no variant meaning
    return bool(tokens & {"cellular", "esim", "lte"})


def extract_chip(text: str) -> str | None:
    # apple silicon designator e.g. "M3" in "iPad Air 13 M3"
    match = re.search(r"\bm([1-9])\b", normalize(text))
    return match.group(0) if match else None


def infer_bare_model(text, other_model):
    # bare numbers are treated as noise by extract_model_number, so "iPhone 16"
    # yields None while "iPhone 16e" yields "16e". that asymmetry skipped the model
    # check entirely, letting a base model match its variant
    if not other_model:
        return None
    digits = ''.join(re.findall(r'\d+', other_model))
    if not digits:
        return None
    return digits if digits in normalize(text).split() else None


def is_accessory(text: str) -> bool:
    # matched on word boundaries — as substrings these caught real devices, e.g.
    # "cover" inside "Galaxy XCover" and "rem" inside "Premium"
    return bool(ACCESSORY_PATTERN.search(text))


def score_match(query, candidate):
    # returns a float 0–1, higher = better match

    # an accessory only matches an accessory. checking the candidate alone let a
    # "Clear Cover" query match the phone itself and take its price
    if is_accessory(candidate) != is_accessory(query):
        return 0.0

    # a standalone "+" (as in "Wi-Fi + Cellular") tokenizes to "plus", which is a
    # tier word and would wrongly disqualify. fused ones like "S25+" are kept
    query = re.sub(r"\s+\+\s+", " ", query)
    candidate = re.sub(r"\s+\+\s+", " ", candidate)

    # "e-SIM" would otherwise tokenize to "e" + "sim" and be missed below
    query = re.sub(r"\be[\s-]?sim\b", "esim", query, flags=re.IGNORECASE)
    candidate = re.sub(r"\be[\s-]?sim\b", "esim", candidate, flags=re.IGNORECASE)

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

    # disqualify if the apple silicon generation differs e.g. iPad Air M3 vs M4.
    # extract_model_number picks the screen size here, so the chip is the only
    # thing distinguishing the generations
    q_chip = extract_chip(query)
    c_chip = extract_chip(candidate)
    if q_chip and c_chip and q_chip != c_chip:
        return 0.0

    # disqualify if the watch case size differs e.g. 40mm vs 44mm
    q_size = extract_case_size(query)
    c_size = extract_case_size(candidate)
    if q_size and c_size and q_size != c_size:
        return 0.0

    # cellular and wi-fi/bluetooth-only are different skus at different prices
    if has_cellular(q_tokens) != has_cellular(c_tokens):
        return 0.0

    # disqualify if model numbers differ e.g. "iPhone 16" vs "iPhone 16e"
    q_model = extract_model_number(query)
    c_model = extract_model_number(candidate)
    # one side may parse a model while the other's is a bare number treated as noise
    if q_model and not c_model:
        c_model = infer_bare_model(candidate, q_model)
    elif c_model and not q_model:
        q_model = infer_bare_model(query, c_model)
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
            # a release year appears on one side only and is not a variant
            if re.fullmatch(r'(19|20)\d{2}', tok):
                continue
            result.add(tok)
        return result

    if _non_storage_digits(candidate, c_storage, c_model) - _non_storage_digits(query, q_storage, q_model):
        return 0.0

    return SequenceMatcher(None, normalize(query), normalize(candidate)).ratio()


def get_market_price(page, product_name):

    # a one-word name ("Signature") matches by chance across unrelated categories.
    # skip rather than store a wrong price
    if len(normalize(clean_search_query(product_name)).split()) < 2:
        log("  -> Name too ambiguous to search, skipping")
        return None, True

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


OUTPUT_PATH = BASE_DIR / 'data' / 'prisjagt' / 'prisjagt_prices.json'

# how often to flush results to disk during a long run
SAVE_EVERY = 10

# reuse a stored price rather than looking it up again if it is newer than this
MAX_PRICE_AGE_DAYS = 3

# bump when score_match changes, so stored results are re-looked-up instead of
# leaving stale wrong prices behind
MATCHER_VERSION = 6


def save_results(results):
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_PATH.with_suffix('.json.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
    tmp.replace(OUTPUT_PATH)  # atomic, so an interrupted write can't corrupt the file


def load_existing_results():
    if not OUTPUT_PATH.exists():
        return {}
    try:
        with OUTPUT_PATH.open(encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def is_fresh(entry):
    entry = entry or {}
    if entry.get('matcher_version') != MATCHER_VERSION:
        return False
    stamp = entry.get('looked_up_at')
    if not stamp:
        return False
    try:
        looked_up = datetime.datetime.strptime(stamp, "%d-%m-%Y-%H:%M")
    except ValueError:
        return False
    return datetime.datetime.now() - looked_up < datetime.timedelta(days=MAX_PRICE_AGE_DAYS)


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

    # resume: keep prices we already have and skip re-looking-up recent ones
    results = load_existing_results()

    # drop entries for products no longer offered. a stale key kept serving an old
    # wrong price to the site because results are looked up by the raw offer name
    stale = [key for key in results if key not in products]
    if stale:
        log(f"Dropping {len(stale)} stale entr(ies) for products no longer offered")
        for key in stale:
            del results[key]

    total = len(products)
    products = [name for name in products if not is_fresh(results.get(name))]
    skipped = total - len(products)
    if skipped:
        log(f"Resuming: {skipped} product(s) already have a price newer than {MAX_PRICE_AGE_DAYS} days")
    log(f"{len(products)} product(s) to look up")

    done = 0
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
            # search under the tidied name, but store under the raw one — the site
            # looks prices up by the product name exactly as the provider wrote it
            search_name = apply_name_substitutions(product_name)
            log(f"Looking up: {search_name}")
            price, page_loaded = get_market_price(page, search_name)

            if not page_loaded:
                consecutive_failures += 1
                log(f"  [failure {consecutive_failures}/{failure_threshold}]")

                if consecutive_failures >= failure_threshold:
                    log(f"\n  !! {failure_threshold} consecutive failures — recycling browser context and pausing 30s...\n")
                    context.close()
                    time.sleep(30)
                    context, page = make_fresh_page(browser)
                    consecutive_failures = 0

                    log(f"  Retrying: {search_name}")
                    price, page_loaded = get_market_price(page, search_name)
                else:
                    # prisjagt throttles in bursts — back off before the next request
                    # instead of spending the remaining attempts against a closed door
                    time.sleep(5 * consecutive_failures)
            else:
                consecutive_failures = 0

            results[product_name] = {
                "market_price": price,
                "looked_up_at": date_time,
                "matcher_version": MATCHER_VERSION
            }
            log(f"  -> {price} kr.")

            # a full run takes hours, so save as we go — an interrupted run keeps
            # what it has and resumes from there
            done += 1
            if done % SAVE_EVERY == 0:
                save_results(results)
                log(f"  [saved {len(results)} results]")

        context.close()
        browser.close()

    save_results(results)

    log(f"\nLooked up {len(results)} products.")


if __name__ == "__main__":
    scrape_prisjagt()