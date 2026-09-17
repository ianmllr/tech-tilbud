import re

# words that exclude a product from being scraped, saved and price-checked.
# matched case-insensitively at the start of a word, so "brugt" also catches
# "Brugte" while "loq" does not match "colloquial"
BLACKLIST_TERMS: list[str] = [
    # used units — prices are not comparable with new goods
    "brugt",
    "refurbished",

    # norlys gaming laptops, too poorly named to extract price data from
    "loq",
    "legion",
    "bærbar",

    # bad naming by oister
    "robotstøvsuger",
]


def _compile() -> list[tuple[str, re.Pattern[str]]]:
    return [(term, re.compile(rf"\b{re.escape(term)}", re.IGNORECASE)) for term in BLACKLIST_TERMS]


_COMPILED = _compile()


def blacklist_match(product_name: str | None) -> str | None:
    # returns the matched term, or None if the name is allowed
    if not product_name:
        return None
    for term, pattern in _COMPILED:
        if pattern.search(product_name):
            return term
    return None


def is_blacklisted(product_name: str | None) -> bool:
    return blacklist_match(product_name) is not None

