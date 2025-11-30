"""
Tools for the assistant backend.

This module defines tools using the Anthropic tool format (no third-party agent frameworks).
"""
import os
import hashlib
import urllib.parse
import httpx
from bs4 import BeautifulSoup
from scrapingbee import ScrapingBeeClient
from typing import Dict, Any, List


def _get_weather_condition(code: int) -> str:
    """Map WMO weather code to human-readable condition."""
    if code == 0:
        return "sunny"
    elif code in [1, 2, 3]:
        return "partly cloudy"
    elif code in [45, 48]:
        return "foggy"
    elif code in [51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82]:
        return "rainy"
    elif code in [71, 73, 75, 77, 85, 86]:
        return "snowy"
    elif code in [95, 96, 99]:
        return "thunderstorm"
    else:
        return "cloudy"


async def web_search(location: str, unit: str = "celsius") -> dict:
    """
    Get the current weather for a city.

    Args:
        location: The city to get weather for
        unit: Temperature unit, either "celsius" or "fahrenheit"

    Returns:
        Weather data including temperature, condition, humidity, and wind speed
    """
    async with httpx.AsyncClient() as client:
        # Step 1: Geocode the location to get coordinates
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={location}&count=1"
        geo_response = await client.get(geo_url)
        geo_data = geo_response.json()

        if not geo_data.get("results"):
            return {"error": f"Location '{location}' not found"}

        lat = geo_data["results"][0]["latitude"]
        lon = geo_data["results"][0]["longitude"]
        resolved_name = geo_data["results"][0]["name"]

        # Step 2: Get weather data
        temp_unit = "fahrenheit" if unit == "fahrenheit" else "celsius"
        weather_url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
            f"&temperature_unit={temp_unit}"
        )
        weather_response = await client.get(weather_url)
        weather_data = weather_response.json()

        current = weather_data.get("current", {})

        # Map weather code to condition
        weather_code = current.get("weather_code", 0)
        condition = _get_weather_condition(weather_code)

        return {
            "location": resolved_name,
            "temperature": current.get("temperature_2m", 0),
            "unit": unit,
            "condition": condition,
            "humidity": current.get("relative_humidity_2m", 0),
            "windSpeed": current.get("wind_speed_10m", 0)
        }


async def _get_usd_jpy_rate() -> float:
    """Fetch current USD to JPY exchange rate."""
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
    """Extract numeric price from price string, converting USD to JPY if needed."""
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


async def searchProducts(query: str, limit: int = 10) -> dict:
    """
    Search for products on Mercari Japan using ScrapingBee.

    Args:
        query: Search term for products (e.g., "Nintendo Switch", "iPhone 15")
        limit: Maximum number of results to return (default: 10)

    Returns:
        DataTable-compatible dict with columns and product data including
        name, price (JPY), condition, seller, and listing URL
    """
    # Generate a unique surface ID based on the query
    query_hash = hashlib.md5(query.encode()).hexdigest()[:8]

    # Define columns for the DataTable
    columns = [
        {"key": "name", "label": "Product", "priority": "primary"},
        {"key": "price", "label": "Price", "format": {"kind": "currency", "currency": "JPY"}},
        {"key": "condition", "label": "Condition"},
        {"key": "seller", "label": "Seller"},
        {"key": "url", "label": "Link", "format": {"kind": "link"}},
    ]

    api_key = os.getenv("SCRAPINGBEE_API_KEY")
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
                "name": name[:100],  # Truncate long names
                "price": price,
                "condition": "-",
                "seller": "-",
                "url": f"https://jp.mercari.com/item/{item_id}",
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
        "name": "web_search",
        "description": "Get the current weather for a city. Returns temperature, condition, humidity, and wind speed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "The city to get weather for (e.g., 'Tokyo', 'New York')"
                },
                "unit": {
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                    "description": "Temperature unit, either 'celsius' or 'fahrenheit'. Defaults to 'celsius'."
                }
            },
            "required": ["location"]
        }
    },
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
    """
    Execute a tool by name with given arguments.

    Args:
        name: The name of the tool to execute
        args: Dictionary of arguments to pass to the tool

    Returns:
        The result from the tool execution
    """
    if name == "web_search":
        return await web_search(
            location=args.get("location", ""),
            unit=args.get("unit", "celsius")
        )
    elif name == "searchProducts":
        return await searchProducts(
            query=args.get("query", ""),
            limit=args.get("limit", 10)
        )
    else:
        return {"error": f"Unknown tool: {name}"}
