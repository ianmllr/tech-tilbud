import requests
from bs4 import BeautifulSoup
import json
import datetime
import re
import os

# setup
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AFFILIATE_PREFIX = "https://go.adt284.net/t/t?a=1666103641&as=2054240298&t=2&tk=1&url="


def download_image(image_url, product_name):
    if not image_url or not product_name:
        return ""

    filename = re.sub(r'[^a-z0-9]', '_', product_name.lower()) + ".webp"
    save_path = os.path.join(BASE_DIR, f"public/images/oister/{filename}")
    os.makedirs(os.path.join(BASE_DIR, "public/images/oister"), exist_ok=True)

    if os.path.exists(save_path):
        return f"/images/oister/{filename}"

    img_response = requests.get(image_url)
    if img_response.status_code == 200:
        with open(save_path, 'wb') as f:
            f.write(img_response.content)
        return f"/images/oister/{filename}"
    return ""


def product_name_from_url(href, fallback_name):

    # the url pattern is always: <subscription-description>-inkl-<product-name>
    # We split on '-inkl-' and take everything after it.
    url = href.rstrip('/').split('/')[-1]  # take last path segment
    if '-inkl-' in url:
        product_part = url.split('-inkl-', 1)[1]
        # title-case each word, upper-casing likely model identifiers (e.g. a11, a8)
        words = product_part.replace('-', ' ').split()
        titled = []
        for w in words:
            # keep all-digit or alphanumeric model codes in their natural case but capitalise first letter
            titled.append(w[0].upper() + w[1:] if w else w)
        return ' '.join(titled)
    return fallback_name


def scrape_oister():
    os.makedirs(os.path.join(BASE_DIR, 'data/oister'), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, 'public/images/oister'), exist_ok=True)

    url = "https://www.oister.dk/tilbehor-til-abonnement"
    response = requests.get(url)
    date_time = datetime.datetime.now().strftime("%d-%m-%Y-%H:%M")

    if response.status_code != 200:
        print(f"Error! Could not fetch the page. Status code: {response.status_code}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')

    offer_list = soup.find_all('div', class_='col--double-padding-bottom')
    promo_card = soup.find('div', class_='section-promo-voice-card')
    if promo_card:
        offer_list = [promo_card] + list(offer_list)

    scraped_data = []

    for offer in offer_list:
        item = {
            "link": "",
            "product_name": "",
            "image_url": "",
            "provider": "Oister",
            "type": "tablet",
            "price_without_subscription": "",
            "price_with_subscription": "",
            "min_cost_6_months": "",
            "subscription_price_monthly": 0,
            "discount_on_product": 0,
            "saved_at": date_time,
        }

        # image url
        image_div = offer.find('div', class_="ribbon-container")
        if image_div:
            # find all images and pick the one with 'tilgift' in src (the actual product)
            all_imgs = image_div.find_all('img')
            for img in all_imgs:
                src = img.get('src') or img.get('data-src') or ''
                if 'tilgift' in src:
                    if src.startswith('/'):
                        item["image_url"] = f"https://www.oister.dk{src}"
                    else:
                        item["image_url"] = src
                    break

        # campaign
        punchline_div = offer.find('div', class_='card__punchline')
        if punchline_div:

            # name of the discounted product - must be before download_image
            strong_tag = punchline_div.find('strong')
            if strong_tag:
                item["product_name"] = strong_tag.get_text(strip=True)
                if "urbanista" in item["product_name"].lower():
                    item["type"] = "sound"

            full_text = punchline_div.get_text(strip=True).replace("inkl. ", "")

            match = re.search(r'\(Værdi\s?(.*?)\)', full_text)

            if match:
                raw_discount = match.group(1).strip()
                clean_number = raw_discount.replace(".", "").replace(",-", "")

                try:
                    item["discount_on_product"] = int(clean_number)
                    item["price_without_subscription"] = int(clean_number)
                    item["price_with_subscription"] = 0
                except ValueError:
                    item["discount_on_product"] = clean_number

        # download image now that we have the product name
        item["image_url"] = download_image(item["image_url"], item["product_name"])

        product_card = offer.find('div', class_='card--product')

        # product link
        if product_card:
            link_tag = product_card.find('a')
            if link_tag:
                href = link_tag.get('href')
                if href:
                    full_link = f"https://www.oister.dk{href}" if href.startswith('/') else href
                    item["link"] = AFFILIATE_PREFIX + full_link
                    # if the punchline name is a generic category label (e.g. "Samsung tablet"),
                    # derive the proper name from the URL -> "Samsung Galaxy Tab A11"
                    GENERIC_LABELS = {'tablet', 'headphones', 'høretelefoner', 'earphones',
                                      'earbuds', 'speaker', 'højttaler', 'watch', 'ur'}
                    last_word = item["product_name"].split()[-1].lower() if item["product_name"] else ''
                    if last_word in GENERIC_LABELS and '-inkl-' in href:
                        better_name = product_name_from_url(href, item["product_name"])
                        if better_name and better_name != item["product_name"]:
                            print(f"  Enriched name from: '{item['product_name']}' -> '{better_name}'")
                            item["product_name"] = better_name


        if product_card:
            options = product_card.find_all('div', class_='card__option')
            if len(options) >= 2:
                pass  # data_gb and talk fields removed — not used by frontend

            all_data_fields = product_card.find_all('h3', class_='card__text-data')
            if len(all_data_fields) >= 3:
                try:
                    price = int(all_data_fields[2].text.strip().replace('.', ''))
                    item["subscription_price_monthly"] = price
                    item["min_cost_6_months"] = price * 6 + 99
                except ValueError:
                    item["subscription_price_monthly"] = all_data_fields[2].text.strip()


        if product_card:
            if "brugt" in item.get("product_name", "").lower():
                print(f"  Skipping used product: {item['product_name']}")
            else:
                scraped_data.append(item)

    with open(os.path.join(BASE_DIR, 'data/oister/oister_offers.json'), 'w', encoding='utf-8') as f:
        json.dump(scraped_data, f, ensure_ascii=False, indent=4)

    print(f"Exported {len(scraped_data)} offers to 'data/oister/oister_offers.json'")


if __name__ == "__main__":
    scrape_oister()