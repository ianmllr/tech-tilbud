"""Tests for picking the market price from a page of search results.

The fixtures are pricerunner search-result cards saved from live lookups
(captured 06-10-2026), so these run without a browser or network.

Run:  py test_market_price_selection.py
"""

import json
import random
from pathlib import Path

import pricerunner_scraper as pricerunner
import prisjagt_scraper as prisjagt
from price_selection import select_market_price, query_coverage, core_similarity

FIXTURES = Path(__file__).resolve().parent / 'fixtures'


def load_cards(name):
    data = json.loads((FIXTURES / name).read_text(encoding='utf-8'))
    cards = [pricerunner.parse_card(c['title'], c['href'], c) for c in data['cards']]
    return data['query'], cards


def no_lookup(href):
    raise AssertionError(f'unexpected detail lookup for {href}')


def pick(query, cards, lookup=no_lookup):
    price, _ = select_market_price(pricerunner.clean_search_query(query), cards, pricerunner.score_match, lookup)
    return price


def test_iphone_18_pro_takes_cheapest_variant():
    # the provider name has no storage, so every iPhone 18 Pro listing matches. a
    # "2TB Blå" card scored 0.826 and moved the score cutoff to 0.776, just above
    # the 256GB cards' 0.7755, so 1TB at 16.990 won instead of 256GB at 10.990
    query, cards = load_cards('pricerunner_apple_iphone_18_pro.json')
    assert any(c['title'] == 'Apple iPhone 18 Pro 2TB Blå' for c in cards)
    assert pick(query, cards) == 10990


def test_iphone_18_pro_independent_of_which_cards_are_listed():
    # which listings appear on the results page changes from day to day. as long
    # as a 10.990 card is among them, the answer must not change
    query, cards = load_cards('pricerunner_apple_iphone_18_pro.json')
    rng = random.Random(7)
    cheapest = [c for c in cards if c['price'] == 10990]
    others = [c for c in cards if c['price'] != 10990]
    for _ in range(200):
        subset = rng.sample(others, rng.randint(0, len(others))) + rng.sample(cheapest, rng.randint(1, len(cheapest)))
        rng.shuffle(subset)
        assert pick(query, subset) == 10990, [c['title'] for c in subset]


def test_card_price_parsing():
    # requiring four digits left every card under 1.000 kr. unpriced, so it only
    # counted if it happened to get one of the detail-page lookups
    assert pricerunner.parse_price_text('999\xa0kr.') == 999
    assert pricerunner.parse_price_text('69\xa0kr.') == 69
    assert pricerunner.parse_price_text('999\xa0kr.\nEller 333\xa0kr./md.') is None
    assert pricerunner.parse_price_text('Eller 3 betalinger af 333\xa0kr.') is None
    assert pricerunner.parse_price_text('1.209\xa0kr.') == 1209
    assert pricerunner.parse_price_text('10.899,00 kr.') == 10899
    assert pricerunner.parse_price_text('849,95 kr.') == 849
    # "Kraftig" starts with "kr" — "Club 120 Kraftig" is not a price of 120
    assert pricerunner.parse_price_text('JBL Partybox Club 120 Kraftig Festhøjttaler') is None
    assert pricerunner.card_price(['JBL Partybox Club 120 Kraftig Festhøjttaler', '5.998\xa0kr.']) == 5998


def test_shop_count_parsing():
    assert pricerunner.parse_shop_count('Apple iPhone 18 Pro 256GB Sort\n10.990 kr.\n9+ butikker') == 9
    assert pricerunner.parse_shop_count('Garmin Venu 4 41mm Displayschutz\n59 kr.\n1 butik') == 1
    assert pricerunner.parse_shop_count('Harman/Kardon Esquire 2\n1.699 kr.\nIkke på lager') == 0
    assert pricerunner.parse_shop_count('Apple iPhone 18 Pro\n10.990 kr.') is None


def test_accessories_and_spare_parts_do_not_set_the_price():
    # with card prices under 1.000 kr. readable, straps, screen protectors and
    # cases for 41-79 kr. pass the model checks and would be the cheapest
    query, cards = load_cards('pricerunner_garmin_venu_4_slate.json')
    assert pick(query, cards) == 3354


def test_single_earbud_does_not_set_the_price():
    # "Apple Venstre Hovedtelefon AirPods 4" is one earbud at 446
    query, cards = load_cards('pricerunner_apple_airpods_4.json')
    assert pick(query, cards) == 895


def test_misfiled_single_shop_listing_does_not_set_the_price():
    # "Samsung Galaxy Watch8 Classic 46 mm - Black" at 69 kr. is one shop's cover
    # filed under the watch. it also showed that "Watch 8" never matched "Watch8"
    query, cards = load_cards('pricerunner_samsung_galaxy_watch8_classic_bt_sort.json')
    assert pick(query, cards) == 1996


def test_phone_cover_does_not_set_the_price():
    query, cards = load_cards('pricerunner_galaxy_s26_fe.json')
    assert pick(query, cards) == 4239


def test_single_shop_listing_only_counts_without_multi_shop_ones():
    query, cards = load_cards('pricerunner_harman_kardon_luna_gra.json')
    # the colourless "Luna" at 999 is sold by one shop; "Luna Grey" by six
    assert pick(query, cards) == 1209
    assert pick(query, [c for c in cards if c['title'] == 'Harman/Kardon Luna']) == 999


def test_unpriced_matching_card_is_looked_up():
    query, cards = load_cards('pricerunner_harman_kardon_luna_gra.json')
    for card in cards:
        if card['title'] == 'Harman/Kardon Luna Grey':
            card['price'] = None
    looked_up = []

    def lookup(href):
        looked_up.append(href)
        return 1209

    assert pick(query, cards, lookup) == 1209
    assert len(looked_up) == 1


def test_unreadable_cheapest_candidate_gives_no_price():
    # a pricier variant must not win just because the cheap card couldn't be read
    query, cards = load_cards('pricerunner_apple_iphone_18_pro.json')
    for card in cards:
        if card['price'] == 10990:
            card['price'] = None

    def failing_lookup(href):
        raise TimeoutError(href)

    assert pick(query, cards, failing_lookup) is None
    # no detail lookups available at all (prisjagt)
    assert pick(query, cards, None) is None
    # the lookups recover it when the detail pages load
    assert pick(query, cards, lambda href: 10990) == 10990
    # more unpriced matches than detail lookups allowed
    for card in cards:
        card['price'] = None
    assert pick(query, cards, lambda href: 10990) is None


def test_out_of_stock_listing_does_not_count():
    cards = [
        {'title': 'Apple iPhone 18 Pro 256GB Sort', 'href': '/a', 'price': 10990, 'shops': 9},
        {'title': 'Apple iPhone 18 Pro 256GB Sølv', 'href': '/b', 'price': 9990, 'shops': 0},
    ]
    assert pick('Apple iPhone 18 Pro', cards) == 10990


def test_listing_missing_part_of_the_name_does_not_compete():
    cards = [
        {'title': 'Harman/Kardon Luna Grey', 'href': '/a', 'price': 1209},
        # no model number and passes every filter, but isn't a Luna
        {'title': 'Harman/Kardon Citation Oasis', 'href': '/b', 'price': 899},
        # another generation, extra words on top of the name
        {'title': 'Harman/Kardon Luna 2 Classic Black', 'href': '/c', 'price': 999},
    ]
    assert pick('Harman Kardon Luna Grå', cards) == 1209


def test_used_listing_does_not_compete():
    cards = [
        {'title': 'Apple iPhone 18 Pro 256GB Sort', 'href': '/a', 'price': 10990},
        {'title': 'Apple iPhone 18 Pro 256GB Sort (Refurbished)', 'href': '/b', 'price': 7990},
    ]
    assert pick('Apple iPhone 18 Pro', cards) == 10990


def test_variant_words_do_not_change_core_similarity():
    base = core_similarity('Apple iPhone 18 Pro', 'Apple iPhone 18 Pro 256GB Sort')
    for title in ['Apple iPhone 18 Pro 2TB Blå', 'Apple iPhone 18 Pro 256 GB - Gletsjerblå',
                  'Apple iPhone 18 Pro 2 TB 5G - Sølv', 'Apple iPhone 18 Pro 1 TB - Bordeaux']:
        assert core_similarity('Apple iPhone 18 Pro', title) == base, title


def test_query_coverage():
    assert query_coverage('Apple iPhone 18 Pro', 'Apple iPhone 18 Pro 256 GB - Sølv') == 1.0
    assert query_coverage('Garmin Fenix 8', 'Garmin fēnix 8 47 mm') == 1.0
    assert query_coverage('Samsung Galaxy Watch9 40mm e-SIM', 'Samsung Galaxy Watch 9 40 mm eSim') == 1.0
    # a colour is a variant, not part of the name
    assert query_coverage('Harman Kardon Luna Grå', 'Harman/Kardon Luna Grey') == 1.0
    assert query_coverage('Harman Kardon Luna Grå', 'Harman/Kardon Citation Oasis') < 1.0


def test_prisjagt_iphone_18_pro():
    # saved from prisjagt.dk 06-10-2026; "1TB"/"2TB" outscore "256GB" here too
    cards = [{'title': t, 'href': None, 'price': prisjagt.parse_card_price(p)} for t, p in [
        ('Apple iPhone 18 Pro 1TB', '16.990\xa0kr.'),
        ('Apple iPhone 18 Pro 2TB', '22.990\xa0kr.'),
        ('Apple iPhone 18 Pro 256GB', '10.990\xa0kr.'),
        ('Apple iPhone 18 Pro 512GB', '12.990\xa0kr.'),
        ('Apple iPhone 18 Pro Max 256GB', '12.290\xa0kr.'),
        ('Apple iPhone 18 Pro Max 1TB', '18.290\xa0kr.'),
    ]]
    query = prisjagt.clean_search_query('Apple iPhone 18 Pro')
    assert select_market_price(query, cards, prisjagt.score_match)[0] == 10990
    assert select_market_price(query, cards[:2] + cards[3:], prisjagt.score_match)[0] == 12990
    cards[2]['price'] = None
    assert select_market_price(query, cards, prisjagt.score_match)[0] is None


if __name__ == "__main__":
    tests = [fn for name, fn in list(globals().items()) if name.startswith('test_')]
    for test in tests:
        test()
    print(f"OK - {len(tests)} tests passed")
