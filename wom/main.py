import asyncio
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Query
from dotenv import load_dotenv
import logging
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
from contextlib import asynccontextmanager
from fastapi.responses import Response
from twitter_analysis import fetch_and_analyze, fetch_stored_tweets
from new_pairs_tracker import fetch_tokens, fetch_tokens_from_db
import requests
import os
import random

BROWSER_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)...",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)...",
    ...
]

load_dotenv()

DISK_PATH = os.getenv("DISK_PATH", "/tmp")  # fallback for local/testing
DB_PATH = os.path.join(DISK_PATH, "tokens.db")

if not os.path.exists(DISK_PATH) and not DISK_PATH.startswith("/data"):
    os.makedirs(DISK_PATH, exist_ok=True)

def init_db(db_path: str = None):
    """Initialize the database and create tables if they don't exist."""
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(db_path)

    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tweets (
            id TEXT PRIMARY KEY,
            token TEXT,
            text TEXT,
            followers_count INTEGER DEFAULT 0,
            user_name TEXT,
            profile_pic TEXT,
            created_at TEXT,
            wom_score REAL
        )
    """)
    # Updated tokens table to include wom_score and tweet_count.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            token_symbol TEXT PRIMARY KEY,
            token_name TEXT,
            address TEXT,
            age_hours REAL,
            volume_usd REAL,
            maker_count INTEGER,
            liquidity_usd REAL,
            market_cap_usd REAL,
            dex_url TEXT,
            priceChange1h REAL,
            wom_score REAL,
            tweet_count INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    logging.info("Database (tokens) initialized successfully.")

def delete_old_tokens():
    """Delete tokens that are older than 24 hours."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get the timestamp of 24 hours ago
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24)
    cursor.execute("DELETE FROM tokens WHERE created_at <= ?", (cutoff_time,))
    conn.commit()
    conn.close()
    logging.info("Deleted old tokens created before %s.", cutoff_time)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("Starting FastAPI App...")
    init_db()  # Initialize the database.
    loop = asyncio.get_running_loop()
    logging.info("Scheduler started.")
    try:
        yield
    finally:
        logging.info("Shutting down FastAPI App...")

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.get("/favicon.ico", include_in_schema=False)
async def ignore_favicon():
    return Response(status_code=204)

# Helper function to fetch tokens from DB including sentiment data.
def fetch_tokens_from_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT token_symbol, age_hours, volume_usd, maker_count,
               liquidity_usd, market_cap_usd, dex_url, priceChange1h, wom_score, tweet_count
        FROM tokens
    """)
    rows = cursor.fetchall()
    conn.close()
    tokens = []
    for row in rows:
        tokens.append({
            "Token": row[0],
            "Age": row[1],
            "Volume": row[2],
            "MakerCount": row[3],
            "Liquidity": row[4],
            "MarketCap": row[5],
            "dex_url": row[6],
            "priceChange1h": row[7],
            "WomScore": row[8],
            "TweetCount":row[9]
        })
    return tokens

# Endpoint to fetch token details from the database.
@app.get("/tokens")
async def get_tokens_details():
    try:
        tokens = fetch_tokens_from_db()
        if not tokens:
            logging.info("No tokens available in the database.")
            return {"message": "No tokens available"}
        return tokens
    except Exception as e:
        logging.error(f"Error fetching token details: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@app.get("/stored-tweets/")
async def get_stored_tweets_endpoint(token: str = Query(..., description="Token symbol")):
    try:
        tweets = await fetch_stored_tweets(token, DB_PATH)
        if not tweets:
            return {"message": f"No stored tweets found for {token}"}
        return {"token": token, "tweets": tweets}
    except Exception as e:
        logging.error(f"Error fetching stored tweets for {token}: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
    
@app.get("/tweet-volume/")
async def get_tweet_volume_endpoint(token: str = Query(..., description="Token symbol")):
    try:
        from twitter_analysis import fetch_tweet_volume_last_6h
        tweet_volume = await fetch_tweet_volume_last_6h(token, DB_PATH)
        return {"token": token, "tweet_volume": tweet_volume}
    except Exception as e:
        logging.error(f"Error fetching tweet volume for {token}: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

from fastapi import Query

@app.get("/run-scheduled-job")
async def run_scheduled_job(key: str = Query(...)):
    expected_key = os.getenv("SCHEDULE_KEY")
    if key != expected_key:
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        delete_old_tokens()  # Clean old records first
        tokens = await fetch_tokens()
        results = []

        for token in tokens:
            result = await fetch_and_analyze(token["token_symbol"], store=True, db_path=DB_PATH)
            results.append(result)

        logging.info(f"Scheduled job completed. {len(results)} tokens processed.")
        return {"message": "Job completed", "processed_tokens": len(results)}
    except Exception as e:
        logging.error(f"Error in scheduled job: {e}")
        raise HTTPException(status_code=500, detail="Scheduled job failed.")


DEX_SCREENER_TOKEN_API = "https://api.dexscreener.com/tokens/v1"

@app.get("/search-token/{chain_id}/{token_address}")
async def search_token(chain_id: str, token_address: str):
    try:
        url = f"{DEX_SCREENER_TOKEN_API}/{chain_id}/{token_address}"

        headers = {
            "User-Agent": random.choice(BROWSER_USER_AGENTS)
        }

        
        logging.info(f"[Dex API] Fetching: {url}")
        response = requests.get(url, headers=headers)
        logging.info(f"[Dex API] Status: {response.status_code}")
        logging.info(f"[Dex API] Body: {response.text[:500]}")  # Trim if large
        
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Dex API error: {response.status_code}")

        data = response.json()

        if not isinstance(data, list) or len(data) == 0:
            raise HTTPException(status_code=404, detail="Token not found")

        token_data = data[0]  # The actual token object
        token_symbol = token_data["baseToken"]["symbol"]

        pair_created_timestamp = token_data.get("pairCreatedAt", 0)
        token_age_hours = round(
            (datetime.now(timezone.utc).timestamp() - (pair_created_timestamp / 1000)) / 3600, 2
        ) if pair_created_timestamp else "N/A"

        return {
            "symbol": token_symbol,
            "priceUsd": token_data.get("priceUsd", "N/A"),
            "marketCap": token_data.get("marketCap", "N/A"),
            "liquidity": token_data.get("liquidity", {}).get("usd", 0),
            "volume24h": token_data.get("volume", {}).get("h24", 0),
            "priceChange1h": token_data.get("priceChange", {}).get("h1", 0),
            "ageHours": token_age_hours,
            "dexUrl": token_data.get("url", "#"),
        }

    except Exception as e:
        logging.error(f"Error in search_token: {e}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

@app.get("/tweets/{token_symbol}")
async def get_tweets(token_symbol: str):
    """
    Fetch tweets **on demand** without storing them.
    """
    try:
        tweets_data = await fetch_and_analyze(token_symbol, store=False, db_path=None)
        
        formatted_tweets = [
            {
                "id": tweet.get("id"),
                "text": tweet.get("text"),
                "user_name": tweet.get("user_name"),
                "followers_count": tweet.get("followers_count", 0),
                "profile_pic": tweet.get("profile_pic", ""),
                "created_at": tweet.get("created_at", ""),
                "wom_score": tweet.get("wom_score", 1.0),
            }
            for tweet in tweets_data.get("tweets", []) 
        ]

        return {
            "token": token_symbol, 
            "tweets": formatted_tweets,
            "wom_score": tweets_data.get("wom_score", 0)
        }
    
    except Exception as e:
        logging.error(f"Error fetching tweets for {token_symbol}: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
