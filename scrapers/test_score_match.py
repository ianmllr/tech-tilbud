"""Tests for score_match, which decides how offers are matched to market prices.

A wrong match produces a wrong market price and shows users an incorrect saving,
so each case below guards a bug that did exactly that.

Run:  py test_score_match.py
"""

import pricerunner_scraper as pricerunner
import prisjagt_scraper as prisjagt

MATCHERS = (("pricerunner", pricerunner.score_match), ("prisjagt", prisjagt.score_match))

# different products — must score 0
MUST_REJECT = [
    # "16" was noise, so the model check skipped and these matched at 0.95
    ("iPhone 16", "iPhone 16e"),
    ("Google Pixel 10 128GB Black", "Google Pixel 10a 128GB Berry"),
    # different chip generation
    ("iPad Air 13 M3 Wi-Fi Cellular 128GB", "Apple iPad Air 13 M4 Wi-Fi 128GB 2026"),
    # wi-fi only is a different sku than wi-fi + cellular
    ("iPad Air 13 M3 Wi-Fi Cellular 128GB", "Apple iPad Air 13 M3 Wi-Fi 128GB"),
    # a fused "+" is part of the model name and must still differentiate
    ("Samsung Galaxy S25", "Samsung Galaxy S25+ 256GB"),
    # case size is a fused "44mm" token the bare-digit check never saw
    ("Samsung Galaxy Watch9 44mm Graphite", "Samsung Galaxy Watch9 40mm Graphite Smartwatch"),
    ("Samsung Galaxy Watch9 44mm e-SIM", "Samsung Galaxy Watch9 44mm BT Graphite"),
    # storage must match when both sides state it
    ("iPhone 16 128GB", "Apple iPhone 16 256GB"),
    ("iPhone 16", "Apple iPhone 16 Silicone Case"),
    # variant qualifiers — these matched and pulled in the cheaper phone's price
    ("Motorola Edge 70", "Motorola Edge 70 Fusion 256GB Smartphone"),
    ("Google Pixel 11 Pro", "Google Pixel 11 Pro XL 256GB"),
    ("Motorola Moto G86", "Motorola Moto G86 Power 256GB"),
    # an accessory query matched the phone itself and took its price
    ("Samsung Galaxy S21 FE Premium Clear Cover - Transparent", "Samsung Galaxy S21 FE 5G 128GB"),
    ("Samsung Galaxy Z Flip4 Silikone cover - Sort", "Samsung Galaxy Z Flip4 256GB"),
    # danish straps and screen protectors outnumber the real watch listings, and
    # one of them passed its price off as the watch's market price
    ("Garmin Venu 4 Slate", "Garmin Venu 4 45mm Metalarmbånd Guld"),
    ("Garmin Venu 4 Slate", "Sunsky Garmin Venu 4 41mm Silikone Urrem"),
    ("Garmin Venu 4 Slate", "Garmin Skærmbeskytter Venu 4 41mm"),
    # a bare model number was treated as a tablet screen size, so the model check
    # was skipped and the base model matched a different one
    ("Google Pixel 11 256GB Black", "Google Pixel 10a 256GB Obsidian"),
]

# same product — must score above 0
MUST_ACCEPT = [
    ("Google Pixel 10 128GB Black", "Google Pixel 10 128GB Obsidian"),
    ("iPhone 16", "Apple iPhone 16 128GB Black"),
    ("Samsung Galaxy A26", "Samsung Galaxy A26 5G 128 GB - Black"),
    # a standalone "+" tokenized to "plus" and rejected every cellular listing
    ("iPad Air 13 M3 Wi-Fi Cellular 128GB", "Apple iPad Air 13 M3 Wi-Fi + Cellular 128GB"),
    ("iPad Pro 11 M5 Wi-Fi Cellular 256GB", "Apple iPad Pro 11 M5 Wi-Fi + Cellular 256GB"),
    ("Samsung Galaxy S25+ 256GB", "Samsung Galaxy S25+ 256GB Navy"),
    ("Samsung Galaxy Watch9 44mm Graphite", "Samsung Galaxy Watch9 44mm Graphite Smartwatch"),
    # a size on only one side is unknown, not a mismatch
    ("Samsung Galaxy Watch9 Graphite", "Samsung Galaxy Watch9 44mm Graphite"),
    # a qualifier present on both sides is still the same phone
    ("Motorola Moto G86 Power", "Motorola Moto G86 Power 256GB 12GB"),
    ("Motorola Edge 70 Fusion", "Motorola Edge 70 Fusion 256GB Smartphone"),
    # an accessory still matches the matching accessory
    ("Samsung Galaxy S21 FE Premium Clear Cover - Transparent", "Samsung Galaxy S21 FE Clear Cover Transparent"),
    # a release year on one side is not a model number or a variant
    ("iPad Air 11 M4 Wi-Fi 128GB", "Apple iPad Air (2026) 11 128GB"),
    ("Samsung Galaxy Watch Ultra Titanium Gray", "Samsung Galaxy Watch Ultra (2025) Titanium Gray"),
    # "cover" inside "XCover" and "rem" inside "Premium" are not accessories
    ("Samsung Galaxy XCover 7 128GB", "Samsung Galaxy XCover 7 5G 128GB Black"),
    ("Garmin Venu 4 Slate", "Garmin Venu 4 41mm Black Smartwatch"),
    ("Google Pixel 11 256GB Black", "Google Pixel 11 256GB Frost"),
]


def test_rejects_different_products():
    for name, score_match in MATCHERS:
        for query, candidate in MUST_REJECT:
            score = score_match(query, candidate)
            assert score == 0.0, f"[{name}] expected 0 for {query!r} vs {candidate!r}, got {score:.2f}"


def test_accepts_same_product():
    for name, score_match in MATCHERS:
        for query, candidate in MUST_ACCEPT:
            score = score_match(query, candidate)
            assert score > 0.0, f"[{name}] expected >0 for {query!r} vs {candidate!r}, got {score:.2f}"


if __name__ == "__main__":
    test_rejects_different_products()
    test_accepts_same_product()
    total = len(MATCHERS) * (len(MUST_REJECT) + len(MUST_ACCEPT))
    print(f"OK - {total} assertions passed")




