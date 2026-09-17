"""Audit the market prices produced by the pricerunner / prisjagt scrapers.

Flags results that are likely wrong so mismatches can be spotted without
eyeballing every row.

Run:  py -u audit_market_prices.py
"""

import glob
import json
import pathlib

BASE = pathlib.Path(__file__).resolve().parent.parent


def load(path):
    return json.loads((BASE / path).read_text(encoding="utf-8"))


pj = load("data/prisjagt/prisjagt_prices.json")
pr = load("data/pricerunner/pricerunner_prices.json")


def coverage(name, data):
    total = len(data)
    priced = sum(1 for v in data.values() if v.get("market_price") is not None)
    print(f"{name:12} {priced}/{total} priced ({priced / total:.0%}), {total - priced} unmatched")


print("=== coverage ===")
coverage("prisjagt", pj)
coverage("pricerunner", pr)

# where both sources priced the same product, they should broadly agree.
# a large gap means at least one of them matched the wrong product.
print("\n=== disagreement between the two sources ===")
both = []
for key in pj:
    a = pj[key].get("market_price")
    b = pr.get(key, {}).get("market_price")
    if a and b:
        ratio = max(a, b) / min(a, b)
        both.append((ratio, key, a, b))
both.sort(reverse=True)
print(f"{len(both)} products priced by both")
bad = [x for x in both if x[0] >= 1.5]
print(f"{len(bad)} disagree by >=50%:")
for ratio, key, a, b in bad[:15]:
    print(f"  x{ratio:.1f}  pj={a:<7} pr={b:<7} {key}")

# a market price far below the offer's own cash price is a red flag:
# it usually means a cheaper variant or an accessory was matched
print("\n=== market price vs provider cash price ===")
index = {}
for src in (pj, pr):
    for key, val in src.items():
        price = val.get("market_price")
        if price is None:
            continue
        low = key.lower()
        index[low] = min(index.get(low, price), price)

suspicious = []
for path in glob.glob(str(BASE / "data" / "*" / "*offers.json")):
    for offer in json.loads(pathlib.Path(path).read_text(encoding="utf-8")):
        name = offer.get("product_name", "")
        market = index.get(name.lower())
        cash = offer.get("price_without_subscription")
        if not market or not isinstance(cash, int) or cash <= 0:
            continue
        # market price less than half the retail price is implausible
        if market < cash * 0.5:
            suspicious.append((market / cash, name, market, cash, offer.get("provider", "?")))

suspicious.sort()
print(f"{len(suspicious)} offers where market price < 50% of provider cash price:")
for ratio, name, market, cash, provider in suspicious[:15]:
    print(f"  {ratio:.0%}  market={market:<7} kontant={cash:<7} [{provider}] {name}")

