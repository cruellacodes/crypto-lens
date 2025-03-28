import asyncio
import os
import sqlite3
import logging
import httpx
from dotenv import load_dotenv

logging.basicConfig(format='[%(levelname)s] %(message)s', level=logging.INFO)
load_dotenv()
api_token = os.getenv("APIFY_API_TOKEN")
if not api_token:
    raise ValueError("Apify API token not found in environment variables!")

DISK_PATH = os.getenv("DISK_PATH", "/tmp")  # fallback for local/testing
DB_PATH = os.path.join(DISK_PATH, "tokens.db")

if not os.path.exists(DISK_PATH) and not DISK_PATH.startswith("/data"):
    os.makedirs(DISK_PATH, exist_ok=True)

async def extract_and_format_symbol(token_symbol_raw):
    """Format the token symbol as a cashtag."""
    try:
        parts = token_symbol_raw.split()
        if len(parts) > 1 and parts[1] in ["DLMM", "CLMM", "CPMM"]:
            symbol = parts[2]
        else:
            symbol = parts[1]
        return f"${symbol.strip()}"
    except (IndexError, AttributeError) as e:
        logging.error(f"Error formatting token symbol from '{token_symbol_raw}': {e}")
        return "$Unknown"

async def get_filtered_pairs():
    """Fetch tokens from Apify and apply filtering criteria."""
    run_input = {
        "chainName": "solana",
        "filterArgs": [
            "?rankBy=trendingScoreH6&order=desc&chainIds=solana&dexIds=raydium,pumpswap,pumpfun&minLiq=50000&minMarketCap=200000&maxAge=48&min24HVol=150000"
        ],
        "fromPage": 1,
        "toPage": 1,
    }
    MIN_MAKERS = 7000
    # MIN_VOLUME = 200_000
    # MIN_MARKET_CAP = 250_000
    # MIN_LIQUIDITY = 100_000
    MAX_AGE = 24  # hours

    filtered_tokens = []
    unique_symbols = set()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.apify.com/v2/acts/crypto-scraper~dexscreener-tokens-scraper/runs?token={api_token}",
            json=run_input,
        )
        response.raise_for_status()
        run_id = response.json()["data"]["id"]

        # Wait for the run to complete
        while True:
            run_status = await client.get(
                f"https://api.apify.com/v2/actor-runs/{run_id}?token={api_token}"
            )
            run_status.raise_for_status()
            status = run_status.json()["data"]["status"]
            if status == "SUCCEEDED":
                break
            elif status in ["FAILED", "TIMED_OUT", "ABORTED"]:
                raise RuntimeError(f"Apify run failed with status: {status}")
            await asyncio.sleep(5)

        # Fetch dataset items
        dataset_id = run_status.json()["data"]["defaultDatasetId"]
        dataset_response = await client.get(
            f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={api_token}"
        )
        dataset_response.raise_for_status()
        items = dataset_response.json()

        logging.info("Processing and filtering fetched token data.")
        for item in items:
            token_name = item.get("tokenName", "Unknown")
            token_symbol_raw = item.get("tokenSymbol", "Unknown")
            token_symbol = await extract_and_format_symbol(token_symbol_raw)
            age = item.get("age", None)
            volume_usd = item.get("volumeUsd", 0)
            maker_count = item.get("makerCount", 0)
            liquidity_usd = item.get("liquidityUsd", 0)
            market_cap_usd = item.get("marketCapUsd", 0)
            priceChange1h = item.get("priceChange1h", 0)
            address = item.get("address", "N/A")

            if (age is not None and age <= MAX_AGE and
                maker_count >= MIN_MAKERS ):
                if token_symbol not in unique_symbols:
                    unique_symbols.add(token_symbol)
                    filtered_tokens.append({
                        "token_name": token_name,
                        "token_symbol": token_symbol,
                        "address": address,
                        "age_hours": age,
                        "volume_usd": volume_usd,
                        "maker_count": maker_count,
                        "liquidity_usd": liquidity_usd,
                        "market_cap_usd": market_cap_usd,
                        "priceChange1h" : priceChange1h,
                        "address" : address
                    })
    logging.info(f"Filtering complete. Total unique tokens: {len(filtered_tokens)}.")
    return filtered_tokens


def store_tokens(tokens):
    """
    Store tokens in the 'tokens' table.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    for token in tokens:
        dex_url = f"https://dexscreener.com/solana/{token.get('address')}"
        cursor.execute("""
            INSERT OR REPLACE INTO tokens (
                token_symbol,
                token_name,
                address,
                age_hours,
                volume_usd,
                maker_count,
                liquidity_usd,
                market_cap_usd,
                dex_url,
                priceChange1h
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            token.get("token_symbol"),
            token.get("token_name"),
            token.get("address"),
            token.get("age_hours"),
            token.get("volume_usd"),
            token.get("maker_count"),
            token.get("liquidity_usd"),
            token.get("market_cap_usd"),
            dex_url,
            token.get("priceChange1h"),
        ))
    conn.commit()
    conn.close()
    logging.info("Tokens stored in the database succesfully.")

def fetch_tokens_from_db(filtered_tokens):
    """
    Fetch only the filtered tokens from the 'tokens' table.
    """
    if not filtered_tokens:
        return []  # Return empty list if no tokens to filter

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    token_addresses = tuple(token["address"] for token in filtered_tokens)

    # Use parameterized query to prevent SQL injection
    query = f"""
        SELECT token_symbol, token_name, address, age_hours, volume_usd, maker_count,
               liquidity_usd, market_cap_usd, dex_url, priceChange1h
        FROM tokens
        WHERE address IN ({",".join(["?"] * len(token_addresses))})
    """

    cursor.execute(query, token_addresses)
    rows = cursor.fetchall()
    conn.close()

    # Convert fetched rows into a list of dictionaries
    tokens = []
    for row in rows:
        tokens.append({
            "token_symbol": row[0],
            "token_name": row[1],
            "address": row[2],
            "age_hours": row[3],
            "volume_usd": row[4],
            "maker_count": row[5],
            "liquidity_usd": row[6],
            "market_cap_usd": row[7],
            "dex_url": row[8],
            "priceChange1h": row[9]
        })
    
    return tokens


async def fetch_tokens():
    """
    Pipeline: Fetch filtered tokens from Apify, store the tokens, and return them.
    """
    filtered_tokens = await get_filtered_pairs()
    if filtered_tokens:
        store_tokens(filtered_tokens)
    else:
        logging.info("No tokens with recent Raydium pools to store.")
    return filtered_tokens
