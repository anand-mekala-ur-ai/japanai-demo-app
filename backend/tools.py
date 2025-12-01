import hashlib
import urllib.parse
from typing import Any

from bs4 import BeautifulSoup
from scrapingbee import ScrapingBeeClient

from config import settings
from models import SearchProductsInput


def _extract_price(price_text: str) -> int:
    import re

    # Extract just the numbers (remove commas, spaces, etc.)
    cleaned = re.sub(r"[^\d]", "", price_text)
    try:
        return int(cleaned) if cleaned else 0
    except ValueError:
        return 0


async def search_products(query: str, limit: int = 3) -> dict:
    # Generate a unique surface ID based on the query
    query_hash = hashlib.md5(query.encode()).hexdigest()[:8]

    # Define columns for the DataTable
    columns = [
        {
            "key": "image",
            "label": "Image",
            "format": {"kind": "image", "width": "160px", "height": "160px"},
        },
        {"key": "name", "label": "Product", "priority": "primary"},
        {"key": "price", "label": "Price"},
        {
            "key": "link",
            "label": "Link",
            "format": {"kind": "link", "hrefKey": "url", "external": True},
        },
    ]

    api_key = settings.SCRAPINGBEE_API_KEY
    if not api_key:
        return {
            "surfaceId": f"mercari-search-{query_hash}",
            "columns": columns,
            "data": [],
            "error": "SCRAPINGBEE_API_KEY not configured",
        }

    try:
        client = ScrapingBeeClient(api_key=api_key)

        # Scrape Mercari Japan search page with JS rendering
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://jp.mercari.com/search?keyword={encoded_query}&status=on_sale"

        response = client.get(
            search_url,
            params={
                "render_js": "true",
                "wait": 5000,  # Wait 5 seconds for JS to load
                "premium_proxy": "true",  # Required for geolocation
                "country_code": "jp",  # Japan proxy for JPY prices
            },
        )

        if response.status_code != 200:
            return {
                "surfaceId": f"mercari-search-{query_hash}",
                "columns": columns,
                "data": [],
                "error": f"ScrapingBee error: {response.status_code}",
            }

        # Parse HTML with BeautifulSoup
        soup = BeautifulSoup(response.content, "html.parser")
        products = []

        # Find product links - Mercari uses anchor tags with /item/ in href
        product_links = soup.select('a[href*="/item/"]')

        seen_ids = set()
        for link in product_links:
            if len(products) >= limit:
                break

            href = link.get("href", "")
            if "/item/" not in href:
                continue

            item_id = href.split("/item/")[-1].split("?")[0]

            # Skip duplicates
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)

            # Extract product name from the link or its children
            name = ""
            # Try to find a span or div with product name
            name_elem = link.select_one('[class*="itemName"], [class*="name"]')
            if name_elem:
                name = name_elem.get_text(strip=True)
            else:
                name = link.get_text(strip=True)

            # Extract image URL
            image_url = ""
            img_elem = link.select_one("img")
            if img_elem:
                image_url = (
                    img_elem.get("src")
                    or img_elem.get("data-src")
                    or img_elem.get("data-lazy-src")
                    or ""
                )
                # Handle relative URLs
                if image_url and not image_url.startswith("http"):
                    if image_url.startswith("//"):
                        image_url = f"https:{image_url}"
                    elif image_url.startswith("/"):
                        image_url = f"https://jp.mercari.com{image_url}"

            # Skip if name is too short or looks like navigation
            if len(name) < 3 or name in ["詳細を見る", "もっと見る"]:
                continue

            # Try to extract price from merPrice span
            price = 0
            is_auction = False
            price_elem = link.select_one('span[class*="merPrice"]')
            if price_elem:
                # Check if this is an auction item (現在 = "current bid")
                currency_elem = price_elem.select_one('span[class*="currency__"]')
                if currency_elem:
                    currency_text = currency_elem.get_text(strip=True)
                    is_auction = "現在" in currency_text

                # Target the number span directly for more reliable extraction
                number_elem = price_elem.select_one('span[class*="number__"]')
                if number_elem:
                    price_text = number_elem.get_text(strip=True)
                    price = _extract_price(price_text)
                else:
                    price = _extract_price(price_elem.get_text(strip=True))

            # Format price string (yen symbol at end)
            if is_auction:
                price_str = f"現在 {price:,}¥"
            else:
                price_str = f"{price:,}¥"

            products.append(
                {
                    "id": item_id,
                    "image": image_url,
                    "name": name[:100],
                    "price": price_str,
                    "link": "View Product",
                    "url": f"https://jp.mercari.com/item/{item_id}",
                }
            )

        return {"surfaceId": f"mercari-search-{query_hash}", "columns": columns, "data": products}

    except Exception as e:
        return {
            "surfaceId": f"mercari-search-{query_hash}",
            "columns": columns,
            "data": [],
            "error": f"Search failed: {str(e)}",
        }


def get_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "search_products",
            "description": (
                "Search for products on Mercari Japan marketplace. "
                "Returns a list of products with name, price (JPY), and listing URL."
            ),
            "input_schema": SearchProductsInput.model_json_schema(),
        }
    ]


async def execute_tool(name: str, args: dict[str, Any]) -> Any:
    if name == "search_products":
        return await search_products(query=args.get("query", ""), limit=args.get("limit", 3))
    else:
        return {"error": f"Unknown tool: {name}"}
