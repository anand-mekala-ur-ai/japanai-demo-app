import hashlib
import urllib.parse
import httpx
from bs4 import BeautifulSoup
from scrapingbee import ScrapingBeeClient
from typing import Dict, Any, List

from config import settings



async def _get_usd_jpy_rate() -> float:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.exchangerate-api.com/v4/latest/USD"
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("rates", {}).get("JPY", 150.0)
    except Exception:
        pass
    return 150.0  # Fallback rate


def _extract_price(price_text: str, usd_to_jpy_rate: float = 150.0) -> int:
    import re

    # Check if it's USD format (US$X.XX)
    if 'US$' in price_text or 'USD' in price_text:
        match = re.search(r'[\d,]+\.?\d*', price_text)
        if match:
            usd_price = float(match.group().replace(',', ''))
            return int(usd_price * usd_to_jpy_rate)
        return 0

    # JPY format: remove ¥, commas, 円
    cleaned = re.sub(r'[¥,\s円]', '', price_text)
    try:
        return int(cleaned)
    except ValueError:
        return 0


async def searchProducts(query: str, limit: int = 3) -> dict:
    # Generate a unique surface ID based on the query
    query_hash = hashlib.md5(query.encode()).hexdigest()[:8]

    # Define columns for the DataTable
    columns = [
        {"key": "image", "label": "Image", "format": {"kind": "image", "width": "160px", "height": "160px"}},
        {"key": "name", "label": "Product", "priority": "primary"},
        {"key": "price", "label": "Price", "format": {"kind": "currency", "currency": "JPY"}},
        {"key": "link", "label": "Link", "format": {"kind": "link", "hrefKey": "url", "external": True}},
    ]

    api_key = settings.SCRAPINGBEE_API_KEY
    if not api_key:
        return {
            "surfaceId": f"mercari-search-{query_hash}",
            "columns": columns,
            "data": [],
            "error": "SCRAPINGBEE_API_KEY not configured"
        }

    try:
        client = ScrapingBeeClient(api_key=api_key)

        # Scrape Mercari Japan search page with JS rendering
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://jp.mercari.com/search?keyword={encoded_query}&status=on_sale"

        response = client.get(
            search_url,
            params={
                'render_js': 'true',
                'wait': 3000,  # Wait 3 seconds for JS to load
            },
        )

        if response.status_code != 200:
            return {
                "surfaceId": f"mercari-search-{query_hash}",
                "columns": columns,
                "data": [],
                "error": f"ScrapingBee error: {response.status_code}"
            }

        # Parse HTML with BeautifulSoup
        soup = BeautifulSoup(response.content, "html.parser")
        products = []

        # Get current exchange rate for USD to JPY conversion
        usd_to_jpy_rate = await _get_usd_jpy_rate()

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
            img_elem = link.select_one('img')
            if img_elem:
                image_url = (
                    img_elem.get('src') or
                    img_elem.get('data-src') or
                    img_elem.get('data-lazy-src') or
                    ''
                )
                # Handle relative URLs
                if image_url and not image_url.startswith('http'):
                    if image_url.startswith('//'):
                        image_url = f"https:{image_url}"
                    elif image_url.startswith('/'):
                        image_url = f"https://jp.mercari.com{image_url}"

            # Skip if name is too short or looks like navigation
            if len(name) < 3 or name in ["詳細を見る", "もっと見る"]:
                continue

            # Try to extract price from merPrice span
            price = 0
            price_elem = link.select_one('span.merPrice')
            if price_elem:
                price = _extract_price(price_elem.get_text(strip=True), usd_to_jpy_rate)

            products.append({
                "id": item_id,
                "image": image_url,
                "name": name[:100],  # Truncate long names
                "price": price,
                "link": "View Product",  # Display text for the link
                "url": f"https://jp.mercari.com/item/{item_id}",  # Actual URL (used by hrefKey)
            })

        return {
            "surfaceId": f"mercari-search-{query_hash}",
            "columns": columns,
            "data": products
        }

    except Exception as e:
        return {
            "surfaceId": f"mercari-search-{query_hash}",
            "columns": columns,
            "data": [],
            "error": f"Search failed: {str(e)}"
        }


# Tool definitions in Anthropic format
TOOLS: List[Dict[str, Any]] = [
    {
        "name": "searchProducts",
        "description": "Search for products on Mercari Japan marketplace. Returns a list of products with name, price (JPY), condition, seller, and listing URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term for products (e.g., 'Nintendo Switch', 'iPhone 15')"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return. Defaults to 10.",
                    "default": 10
                }
            },
            "required": ["query"]
        }
    }
]


async def execute_tool(name: str, args: Dict[str, Any]) -> Any:
    if name == "searchProducts":
        return await searchProducts(
            query=args.get("query", ""),
            limit=args.get("limit", 3)
        )
    else:
        return {"error": f"Unknown tool: {name}"}
