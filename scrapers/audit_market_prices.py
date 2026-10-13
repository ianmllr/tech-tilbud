"""Audit the market prices produced by the pricerunner / prisjagt scrapers.

Flags results that are likely wrong so mismatches can be spotted without
eyeballing every row. Read-only: it never changes data and makes no network calls.

Every flagged line names the rule that matched:
  A  market price > 1.3x the lowest cash price among offers with that exact name
  B  market price < 65% of a provider's cash price (report only, real sales happen)
  C  pricerunner and prisjagt disagree by 1.35x or more
  D  pricerunner names that differ only by brand, storage, colour or spacing
     but have different pricerunner prices

Run:  py -u audit_market_prices.py
"""

import glob
import json
import pathlib
import re
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent

ABOVE_CASH_RATIO = 1.3
BELOW_CASH_RATIO = 0.65
DISAGREE_RATIO = 1.35

BRANDS = {
    "apple", "samsung", "google", "motorola", "jbl", "garmin", "oppo", "sony", "sonos",
    "oneplus", "doro", "tcl", "zte", "lenovo", "nintendo", "beyerdynamic", "harman",
    "kardon", "urbanista", "panzerglass", "xiaomi", "nothing", "honor", "huawei",
    "realme", "fairphone", "nokia",
}
COLOURS = {
    "black", "sort", "white", "hvid", "grey", "gray", "grå", "graphite", "grafit",
    "silver", "sølv", "midnight", "starlight", "gold", "guld", "blue", "blå", "green",
    "grøn", "orange", "pink", "rose", "red", "rød", "purple", "lilla", "yellow", "gul",
    "obsidian", "onyx", "titanium", "space", "lunar", "sand", "beige", "burgundy",
    "olive", "bronze", "glacier", "moss", "canyon", "ocean", "ice", "jet", "silk",
    "aurora", "arctic", "slate", "dark", "light", "clear", "translucent", "lavender",
    "mint", "cream", "natural", "desert", "teal", "cosmic", "phantom", "navy",
}


def load(path):
    return json.loads((BASE / path).read_text(encoding="utf-8"))


def price(entry):
    return (entry or {}).get("market_price")


def norm_name(name):
    # strip brand, storage, colour, "med abonnement" and spacing so variants group together
    low = name.lower().replace("med abonnement", " ")
    low = re.sub(r"\d+\s*(gb|tb)\b", " ", low)
    words = [w for w in re.split(r"[\s,/()]+", low) if w]
    words = [w for w in words if w not in BRANDS and w not in COLOURS]
    return "".join(words)


def load_offers():
    # every offer from every provider file, with the folder name as provider fallback
    offers = []
    for path in sorted(glob.glob(str(BASE / "data" / "*" / "*offers.json"))):
        folder = pathlib.Path(path).parent.name
        for offer in json.loads(pathlib.Path(path).read_text(encoding="utf-8")):
            offer.setdefault("provider", None)
            offers.append((offer, offer.get("provider") or folder))
    return offers


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    pj = load("data/prisjagt/prisjagt_prices.json")
    pr = load("data/pricerunner/pricerunner_prices.json")
    offers = load_offers()

    print("=== coverage ===")
    for name, data in (("prisjagt", pj), ("pricerunner", pr)):
        total = len(data)
        priced = sum(1 for v in data.values() if price(v) is not None)
        print(f"{name:12} {priced}/{total} priced ({priced / total:.0%}), {total - priced} with price None")

    # lowest cash price per name, matched case-insensitively like offers.ts
    lowest_cash = {}
    for offer, _ in offers:
        cash = offer.get("price_without_subscription")
        if not isinstance(cash, int) or cash <= 0:
            continue
        low = offer.get("product_name", "").lower()
        lowest_cash[low] = min(lowest_cash.get(low, cash), cash)

    print(f"\n=== A: market price > {ABOVE_CASH_RATIO}x lowest cash price (same name) ===")
    rule_a = []
    for src_name, src in (("pricerunner", pr), ("prisjagt", pj)):
        for key, val in src.items():
            market = price(val)
            cash = lowest_cash.get(key.lower())
            if market and cash and market > cash * ABOVE_CASH_RATIO:
                rule_a.append((src_name, key, market, cash))
    print(f"{len(rule_a)} flagged:")
    for src_name, key, market, cash in sorted(rule_a, key=lambda x: (x[0], -x[2] / x[3])):
        print(f"  [A] {src_name:11} market={market:<7} lowest_cash={cash:<7} x{market / cash:.2f}  {key}")

    # the site only uses pricerunner for now (prisjagt is commented out in offers.ts),
    # so B checks that value
    index = {}
    for src in (pr,):
        for key, val in src.items():
            market = price(val)
            if market is None:
                continue
            low = key.lower()
            index[low] = min(index.get(low, market), market)

    print(f"\n=== B: market price < {BELOW_CASH_RATIO:.0%} of provider cash price (report only) ===")
    rule_b = set()
    for offer, provider in offers:
        name = offer.get("product_name", "")
        market = index.get(name.lower())
        cash = offer.get("price_without_subscription")
        if not market or not isinstance(cash, int) or cash <= 0:
            continue
        if market < cash * BELOW_CASH_RATIO:
            rule_b.add((market / cash, name, market, cash, provider))
    print(f"{len(rule_b)} flagged:")
    for ratio, name, market, cash, provider in sorted(rule_b):
        print(f"  [B] {ratio:.0%}  market={market:<7} cash={cash:<7} [{provider}] {name}")

    print(f"\n=== C: pricerunner vs prisjagt disagree by >= {DISAGREE_RATIO}x (prisjagt is not used by the site right now) ===")
    both = []
    for key in pj:
        a = price(pj[key])
        b = price(pr.get(key))
        if a and b:
            both.append((max(a, b) / min(a, b), key, a, b))
    rule_c = sorted((x for x in both if x[0] >= DISAGREE_RATIO), reverse=True)
    print(f"{len(rule_c)} of {len(both)} products priced by both flagged:")
    for ratio, key, a, b in rule_c:
        print(f"  [C] x{ratio:.2f}  prisjagt={a:<7} pricerunner={b:<7} {key}")

    print("\n=== D: name variants with different pricerunner prices ===")
    groups = {}
    for key, val in pr.items():
        groups.setdefault(norm_name(key), []).append((key, price(val)))
    rule_d = []
    for norm, members in groups.items():
        prices = {p for _, p in members if p is not None}
        if len(members) > 1 and len(prices) > 1:
            rule_d.append((norm, sorted(members)))
    print(f"{len(rule_d)} groups flagged:")
    for norm, members in sorted(rule_d):
        print(f"  [D] group '{norm}':")
        for key, p in members:
            print(f"        pricerunner={str(p):<7} {key}")


if __name__ == "__main__":
    main()
