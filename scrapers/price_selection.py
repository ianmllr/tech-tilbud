"""Picks the market price from a page of search results.

Shared by the pricerunner and prisjagt scrapers. Kept free of playwright so the
selection can be tested against saved cards without a browser.

The market price is the lowest price of any listing that is the product. Which
listings are the product:
  1. the scraper's score_match passes (model, tier, storage, size, accessory, ...)
  2. the listing names the product as well as the best listing does, judged on the
     names with variant words (storage, size, colour, connectivity) taken out
  3. the listing is in stock, and not a single-shop listing when listings sold by
     several shops exist — single-shop pages are where misfiled marketplace items
     live, e.g. a 69 kr. cover listed as "Galaxy Watch8 Classic 46 mm - Black"

The fuzzy similarity of the full names is deliberately not used to compare
listings: it rewards short titles, so "2TB Blå" outscored "256GB Sort", pushed the
cheap variant out of the comparison, and which variant won then depended on which
listings happened to be on the results page that day.
"""

import os
import re
import unicodedata
from difflib import SequenceMatcher

from scraper_utils import log, is_blacklisted

# the best match must reach this similarity, otherwise the page holds nothing like
# the product and every candidate is a coincidence
MIN_BEST_SCORE = 0.4

# a listing's name (variant words removed) must be at least this similar, relative
# to the best listing's, to count as the product. keeps out "Apple Venstre
# Hovedtelefon AirPods 4" and "INF TPU Schutzhülle Garmin Venu 4"
CORE_WINDOW = 0.85

# cards without a readable price get a detail-page lookup. more than this many and
# the cheapest listing can't be established, so no price is stored
MAX_DETAIL_LOOKUPS = 5

# set MARKET_PRICE_DEBUG=1 to log every candidate on the results page
DEBUG = os.environ.get('MARKET_PRICE_DEBUG') == '1'

# words that only pick a variant of the same product. storage and case size are
# checked by score_match; colour never changes which product it is
VARIANT_WORDS = {
    # colours
    'black', 'white', 'silver', 'gray', 'grey', 'blue', 'red', 'green', 'gold', 'pink',
    'purple', 'yellow', 'orange', 'beige', 'cream', 'navy', 'mint', 'sand', 'teal', 'coral',
    'lavender', 'violet', 'lilac', 'rose', 'plum', 'champagne', 'copper', 'bronze', 'aqua',
    'sky', 'sage', 'olive', 'ice', 'icy', 'ink', 'jet', 'graphite', 'grafit', 'titanium',
    'obsidian', 'midnight', 'starlight', 'pistachio', 'blueberry', 'bordeaux', 'burgundy',
    'slate', 'moss', 'frost', 'porcelain', 'hazel', 'iris', 'peony', 'lemongrass', 'phantom',
    'shadow', 'moonstone', 'charcoal', 'natural', 'desert', 'space', 'cosmic', 'jetblack',
    'glacier', 'sunrise', 'ocean', 'blush', 'dark', 'light', 'deep', 'soft', 'sort', 'hvid',
    'sølv', 'grå', 'blå', 'rød', 'grøn', 'gul', 'guld', 'lilla', 'rosa', 'brun', 'sorte',
    # connectivity and generic descriptors
    'smartphone', 'mobiltelefon', 'smartwatch', '5g', '4g', 'lte', 'dual', 'sim', 'esim',
    'bt', 'bluetooth', 'wifi', 'wi', 'fi', 'gps', 'cellular',
}

# danish compound colours: "Gletsjerblå", "Lyserød", "Rosaguld"
COLOUR_SUFFIX = re.compile(r'.+(blå|rød|grøn|grå|hvid|sort|gul|guld|lilla|rosa|sølv)$')


def _strip_accents(text):
    # "fēnix" and "fenix" are the same word. keeps the ring of å, a letter of its own
    kept = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c) or c == '\u030a')
    return unicodedata.normalize('NFC', kept)


def match_words(text):
    # the words of a name as compared here
    text = _strip_accents(text.lower())
    text = re.sub(r"\s+\+\s+", " ", text)
    text = re.sub(r"\be[\s-]?sim\b", "esim", text)
    text = re.sub(r'\+', ' plus ', text)
    # storage, ram and case size, with or without a space
    text = re.sub(r'\b\d+\s*(gb|tb|mb)\b', ' ', text)
    text = re.sub(r'\b\d{2}\s*mm\b', ' ', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    return text.split()


def core_words(text):
    # the name without words that only pick a variant
    return [w for w in match_words(text) if w not in VARIANT_WORDS and not COLOUR_SUFFIX.fullmatch(w)]


def _word_parts(word):
    # "watch9" -> {"watch", "9"}, so "Watch9" and "Watch 9" are the same
    return set(re.findall(r'[a-zæøå]+|\d+', word))


def query_coverage(query, candidate):
    # share of the query's core words found in the candidate
    q_words = core_words(query)
    if not q_words:
        return 1.0
    c_words = set()
    for word in match_words(candidate):
        c_words.add(word)
        c_words.update(_word_parts(word))
    covered = sum(1 for w in q_words if w in c_words or _word_parts(w) <= c_words)
    return covered / len(q_words)


def core_similarity(query, candidate):
    # similarity of the names with variant words removed. all colour and storage
    # variants of a product score the same; extra words that make it something
    # else (a strap, a case, a single earbud) still lower it
    return SequenceMatcher(None, ' '.join(core_words(query)), ' '.join(core_words(candidate))).ratio()


def select_market_price(query, cards, score_match, lookup_price=None):
    """Returns (price, report) for the cheapest listing matching the query.

    cards: list of dicts with 'title', 'href', 'price' (int, or None when the card
    shows no readable price) and optionally 'shops' (number of shops; 0 when out of
    stock; None or missing when unknown).
    lookup_price: optional callable(href) -> int | None for cards without a price;
    it raises when the page can't be loaded.
    report: lines describing the decision, for the scrape log.
    """
    rows = []
    for card in cards:
        title = card['title']
        score = score_match(query, title)
        # a used or refurbished listing is cheaper than any new unit
        if score > 0 and is_blacklisted(title) and not is_blacklisted(query):
            score = 0.0
        rows.append({**card, 'score': score, 'coverage': query_coverage(query, title),
                     'core': core_similarity(query, title), 'note': ''})

    def fail(reason):
        return None, [reason] + _debug_table(rows)

    matching = [r for r in rows if r['score'] > 0.0]
    if not matching:
        return fail('All candidates disqualified')

    best_score = max(r['score'] for r in matching)
    if best_score < MIN_BEST_SCORE:
        return fail(f'Best score {best_score:.2f} below threshold, skipping')

    # listings that leave out part of the name (the product line) are weaker
    # matches than ones that carry all of it
    best_coverage = max(r['coverage'] for r in matching)
    matching = [r for r in matching if r['coverage'] == best_coverage]
    best_core = max(r['core'] for r in matching)
    matching = [r for r in matching if r['core'] >= best_core * CORE_WINDOW]

    # out of stock: the price shown isn't one anybody sells at
    for r in matching:
        if r.get('shops') == 0:
            r['note'] = 'out of stock'
    matching = [r for r in matching if r.get('shops') != 0]
    if any((r.get('shops') or 0) >= 2 for r in matching):
        for r in matching:
            if r.get('shops') == 1:
                r['note'] = 'single shop'
        matching = [r for r in matching if r.get('shops') != 1]

    for r in matching:
        r['selected'] = True

    # a card without a readable price might be the cheapest, so look it up rather
    # than letting a pricier variant win
    unpriced = [r for r in matching if r['price'] is None]
    if unpriced:
        if lookup_price is None or len(unpriced) > MAX_DETAIL_LOOKUPS:
            return fail(f'{len(unpriced)} matching card(s) without a readable price, cannot tell the cheapest')
        for r in unpriced:
            try:
                r['detail_price'] = lookup_price(r['href'])
            except Exception:
                return fail(f"Could not load detail page for '{r['title']}', cannot tell the cheapest")
            # a loaded page without a price has no sellers — nothing to compare
            r['price'] = r['detail_price']

    priced = [r for r in matching if r['price'] is not None]
    if not priced:
        return fail('No prices among matching candidates')

    priced.sort(key=lambda r: (r['price'], -r['score']))
    winner = priced[0]
    winner['winner'] = True

    others = ', '.join(f"{r['price']} '{r['title']}'" for r in priced[1:4])
    more = f' (+{len(priced) - 4} more)' if len(priced) > 4 else ''
    line = (f"Matched: '{winner['title']}' (score={winner['score']:.2f}) at {winner['price']} kr., "
            f"cheapest of {len(priced)} matching" + (f"; next: {others}{more}" if others else ''))
    return winner['price'], [line] + _debug_table(rows)


def _debug_table(rows):
    if not DEBUG:
        return []
    lines = ['  candidates (* winner, + compared): score coverage core shops price detail title']
    for r in sorted(rows, key=lambda r: (-r['score'], r['title'])):
        mark = '*' if r.get('winner') else ('+' if r.get('selected') else ' ')
        lines.append(f"  {mark} {r['score']:.3f} {r['coverage']:.2f} {r['core']:.2f} {r.get('shops')!s:>4} "
                     f"{r['price']!s:>6} {r.get('detail_price', '-')!s:>6}  {r['title']}"
                     + (f"  [{r['note']}]" if r['note'] else ''))
    return lines


def log_report(report):
    for line in report:
        log(line)
