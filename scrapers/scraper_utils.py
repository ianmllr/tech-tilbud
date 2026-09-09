import datetime
import json
import re
import builtins
from pathlib import Path
from typing import Any

import requests

from product_blacklist import blacklist_match, is_blacklisted

__all__ = [
    "apply_name_substitutions",
    "blacklist_match",
    "download_image_cached",
    "error",
    "is_blacklisted",
    "log",
    "now_timestamp",
    "offer_summary",
    "skip_if_blacklisted",
    "warn",
    "write_json",
]

# manual substitutions for product names that are too inconsistent to reliably parse price data from. the keys are regex
# patterns that are applied to the raw product name, and the values are the normalized product names that are used for
# price extraction
PRODUCT_NAME_SUBSTITUTIONS = {
    # iPad Pro 11 inch
    r"Apple iPad Pro 11\" M5 \(2025\) WiFi \+ Cellular 256GB": "iPad Pro 11 M5 Wi-Fi Cellular 256GB",
    r"Apple iPad Pro 11\" M5 \(2025\) WiFi 256GB": "iPad Pro 11 M5 Wi-Fi 256GB",
    r"Apple iPad Pro 11\" M4 \(2024\) WiFi \+ Cellular 256GB": "iPad Pro 11 M4 Wi-Fi Cellular 256GB",

    # iPad Pro 13 inch
    r"Apple iPad Pro 13\" M5 \(2025\) WiFi \+ Cellular 256GB": "iPad Pro 13 M5 Wi-Fi Cellular 256GB",
    r"Apple iPad Pro 13\" M5 \(2025\) WiFi 256GB": "iPad Pro 13 M5 Wi-Fi 256GB",

    # iPad (base model) 11 inch
    r"Apple iPad 11\" A16 \(2025\) WiFi \+ Cellular 128GB": "iPad 11 A16 Wi-Fi Cellular 128GB",
    r"Apple iPad 11\" A16 \(2025\) WiFi 128GB": "iPad 11 A16 Wi-Fi 128GB",

    # iPad Air 11 inch
    r"Apple iPad Air 11\" M4 \(2026\) WiFi \+ Cellular 128GB": "iPad Air 11 M4 Wi-Fi Cellular 128GB",
    r"Apple iPad Air 11\" M4 \(2026\) WiFi 128GB": "iPad Air 11 M4 Wi-Fi 128GB",
    r"Apple iPad Air 11\" M3 \(2025\) WiFi \+ Cellular 128GB": "iPad Air 11 M3 Wi-Fi Cellular 128GB",
    r"Apple iPad Air 11\" M3 \(2025\) WiFi 128GB": "iPad Air 11 M3 Wi-Fi 128GB",

    # iPad Air 13 inch
    r"Apple iPad Air 13\" M4 \(2026\) WiFi \+ Cellular 128GB": "iPad Air 13 M4 Wi-Fi Cellular 128GB",
    r"Apple iPad Air 13\" M4 \(2026\) WiFi 128GB": "iPad Air 13 M4 Wi-Fi 128GB",
    r"Apple iPad Air 13\" M3 \(2025\) WiFi \+ Cellular 128GB": "iPad Air 13 M3 Wi-Fi Cellular 128GB",
    r"Apple iPad Air 13\" M3 \(2025\) WiFi 128GB": "iPad Air 13 M3 Wi-Fi 128GB",
    r"Apple iPad Air 13\" M2 \(2024\) WiFi \+ Cellular 128GB": "iPad Air 13 M2 Wi-Fi Cellular 128GB",

    # Apple Watch Series 11
    r"Apple Watch Series 11 GPS \+ Cellular 46mm Rose Gold Aluminium Case with Light Blush Sport Band M/L": "Apple Watch Series 11 GPS + Cellular 46mm M/L",
    r"Apple Watch Series 11 GPS \+ Cellular 46mm Slate Titanium Case with Slate Milanese Loop M/L": "Apple Watch Series 11 GPS + Cellular 46mm M/L",
    r"Apple Watch Series 11 GPS 46mm Jet Black Aluminium Case with Black Sport Band M/L": "Apple Watch Series 11 GPS 46mm M/L",
    r"Apple Watch Series 11 GPS \+ Cellular 42mm Gold Titanium Case with Gold Milanese Loop": "Apple Watch Series 11 GPS + Cellular 42mm M/L",

    # other device substitutions
    "Samsung Galaxy Watch8 40mm eSIM - Grafit": "Samsung Galaxy Watch8 40mm LTE",
}

# apply manual substitution
def apply_name_substitutions(product_name):
    import re
    if not product_name:
        return product_name

    result = product_name
    for pattern, replacement in PRODUCT_NAME_SUBSTITUTIONS.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    return result.strip()



def now_timestamp() -> str:
    return datetime.datetime.now().strftime("%d-%m-%Y-%H:%M")


def skip_if_blacklisted(product_name: str | None, *, indent: str = "  ") -> bool:
    """Return True (and log why) when a product is globally blacklisted."""
    matched = blacklist_match(product_name)
    if matched:
        log(f"{indent}Skipping blacklisted product ('{matched}'): {product_name}")
        return True
    return False


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def _normalize_image_url(image_url: str, base_url: str | None) -> str:
    if image_url.startswith("//"):
        return f"https:{image_url}"
    if image_url.startswith("/") and base_url:
        return f"{base_url}{image_url}"
    return image_url


def log(*args: Any, sep: str = " ", end: str = "\n") -> None:
    message = sep.join(str(a) for a in args)
    leading_newlines = len(message) - len(message.lstrip("\n"))
    stripped = message.lstrip("\n")
    if not stripped.startswith("["):
        stripped = f"{stripped}"
    builtins.print(("\n" * leading_newlines) + stripped, end=end)


def warn(*args: Any, sep: str = " ", end: str = "\n") -> None:
    message = sep.join(str(a) for a in args)
    builtins.print(f"[WARN] {message.lstrip()}", end=end)


def error(*args: Any, sep: str = " ", end: str = "\n") -> None:
    message = sep.join(str(a) for a in args)
    builtins.print(f"[ERROR] {message.lstrip()}", end=end)


def offer_summary(
    product_name: str,
    *,
    sub: Any = None,
    rabat: Any = None,
    kontant: Any = None,
    min6: Any = None,
    md: Any = None,
) -> None:
    def fmt(value: Any) -> str:
        return "-" if value in (None, "") else str(value)

    log(
        f"{product_name}: "
        f"sub={fmt(sub)}, rabat={fmt(rabat)}, kontant={fmt(kontant)}, "
        f"min6={fmt(min6)}, md={fmt(md)}"
    )


def download_image_cached(
    image_url: str,
    product_name: str,
    image_dir: Path,
    public_prefix: str,
    *,
    base_url: str | None = None,
    timeout: int = 15,
) -> str:
    if not image_url or not product_name:
        return ""

    image_url = _normalize_image_url(image_url, base_url)

    filename = re.sub(r"[^a-z0-9]", "_", product_name.lower()) + ".webp"
    save_path = image_dir / filename
    image_dir.mkdir(parents=True, exist_ok=True)

    normalized_prefix = public_prefix.rstrip("/")
    cached_path = f"{normalized_prefix}/{filename}"

    if save_path.exists():
        return cached_path

    try:
        response = requests.get(image_url, timeout=timeout)
        if response.status_code == 200:
            save_path.write_bytes(response.content)
            return cached_path
    except Exception as e:
        warn(f"Could not download image for '{product_name}': {e}")

    return ""

