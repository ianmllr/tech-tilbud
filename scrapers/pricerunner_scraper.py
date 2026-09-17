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
TIER_WORDS = {'ultra', 'cellular', 'aktiv støjreduktion', 'anc', 'plus', 'pro', 'max', 'mini', 'fe', 'fold', 'flip', 'lite', 'edge', 'air',
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
             'dark', 'true', 'on', 'ear', 'tws', 'gen', 'space black', 'wifi'}
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


def extract_case_size(text):
    # watch case size e.g. "44" from "Galaxy Watch9 44mm". written as a fused
    # token, so the bare-digit check in score_match never sees it
    match = re.search(r"\b(\d{2})\s*mm\b", text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def has_cellular(tokens):
    # cellular/lte variant rather than wi-fi or bluetooth only. excludes "5g"/"4g",
    # which appear on nearly every phone listing and carry no variant meaning
    return bool(tokens & {"cellular", "esim", "lte"})


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


def extract_chip(text):
    # apple silicon designator e.g. "M3" in "iPad Air 13 M3"
    match = re.search(r'\bm([1-9])\b', normalize(text))
    return match.group(0) if match else None


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

    # disqualify if the apple silicon generation differs e.g. iPad Air M3 vs M4
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
            # a release year appears on one side only and is not a variant
            if re.fullmatch(r'(19|20)\d{2}', tok):
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


def extract_jsonld_low_price(page) -> int | None:
    # the detail page embeds an AggregateOffer with the lowest price for new units.
    # this is the most reliable source — structured and immune to layout changes
    try:
        blocks = page.eval_on_selector_all(
            'script[type="application/ld+json"]',
            "els => els.map(e => e.textContent)",
        )
    except Exception:
        return None

    for raw in blocks:
        if not raw or 'lowPrice' not in raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        offers = data.get('offers') if isinstance(data, dict) else None
        low = (offers or {}).get('lowPrice')
        if low is None:
            continue
        try:
            return int(float(low))
        except (TypeError, ValueError):
            continue
    return None


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


def extract_header_price(page) -> int | None:
    # single-seller pages carry no AggregateOffer and no "Laveste pris" label,
    # only a "Pris" heading above the amount
    try:
        text = page.inner_text("body")
    except Exception:
        return None

    match = re.search(r'(?:^|\n)\s*Pris\s*\n\s*(\d{1,3}(?:\.\d{3})+|\d{3,})\s*kr', text, flags=re.IGNORECASE)
    return int(match.group(1).replace('.', '')) if match else None


def get_market_price(page, product_name):

    # a one-word name ("Signature") matches by chance across unrelated categories —
    # it found a RØDE microphone. skip rather than store a wrong price
    if len(normalize(clean_search_query(product_name)).split()) < 2:
        log("Name too ambiguous to search, skipping")
        return None, True

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

        # collect price-like text belonging to this card only. climbing a fixed
        # number of ancestors used to escape the card and pick up neighbouring
        # cards' prices, which then won as the cheapest candidate
        price_texts = []
        try:
            price_texts = link.evaluate("""el => {
                // widen the scope while the ancestor still holds only this product link
                let scope = el;
                let node = el.parentElement;
                for (let i = 0; i < 6; i++) {
                    if (!node) break;
                    if (node.querySelectorAll('a[href^="/pl/"]').length > 1) break;
                    scope = node;
                    node = node.parentElement;
                }
                const found = [];
                for (const s of scope.querySelectorAll('span, div, p, li')) {
                    const t = (s.innerText || s.textContent || '').trim();
                    if (/\\d/.test(t) && t.toLowerCase().includes('kr') && t.length < 60 && !t.startsWith('-')) {
                        found.push(t);
                    }
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
            # structured data first, then the explicit "Laveste pris" label, then the
            # "Pris" heading used on single-seller pages. the old min(offer rows)
            # fallback picked up cross-sells and used listings — no price beats a
            # wrong price
            detail_price = extract_jsonld_low_price(page)
            if detail_price is None:
                detail_price = laveste
            if detail_price is None:
                detail_price = extract_header_price(page)
            detail_price_map[href] = {'price': detail_price}
        except Exception:
            # if a detail page fails, ignore and continue with card price
            pass

    # resolve one price per candidate. the detail-page figure is the most reliable
    # when it loads, but the search-card price is what a person actually sees when
    # searching, so keep whichever is lower. the cheapest candidate wins — the base
    # (smallest-storage) variant is the cheapest, and when the provider name omits
    # storage this picks the right price instead of guessing a storage tier from a
    # title that may not carry one.
    priced = []
    for score, title, href, price_texts, parsed_price in top_candidates:
        detail = (detail_price_map.get(href, {}) or {}).get('price')
        options = [p for p in (parsed_price, detail) if p is not None]
        if not options:
            options = [p for p in (parse_price_text(pt) for pt in price_texts) if p is not None]
        if options:
            priced.append((score, title, href, min(options)))

    if not priced:
        log("No parseable prices among top candidates")
        return None, True

    # only compare candidates that match about as well as the best-scored one, so a
    # much weaker but cheaper match can't drag the market price below the real one
    top_score = max(p[0] for p in priced)
    priced = [p for p in priced if p[0] >= top_score - SCORE_TOLERANCE]
    priced.sort(key=lambda x: (x[3], -x[0]))
    best_score, best_title, best_href, best_price = priced[0]

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


OUTPUT_PATH = BASE_DIR / 'data' / 'pricerunner' / 'pricerunner_prices.json'

# how often to flush results to disk during a long run
SAVE_EVERY = 10

# reuse a stored price rather than looking it up again if it is newer than this
MAX_PRICE_AGE_DAYS = 3

# bump when score_match or price extraction changes, so stored results are
# re-looked-up instead of leaving stale wrong prices behind
MATCHER_VERSION = 9

# when several candidates match, only compare prices among those scoring within
# this much of the best one
SCORE_TOLERANCE = 0.05


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
                    # recycle the browser context to recover from a potential block
                    log(f"\n  !! {failure_threshold} consecutive failures — recycling browser context and pausing 10s...\n")
                    context.close()
                    time.sleep(10)
                    context, page = make_fresh_page(browser)
                    consecutive_failures = 0

                    log(f"  Retrying: {search_name}")
                    price, page_loaded = get_market_price(page, search_name)
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
    scrape_pricerunner()

