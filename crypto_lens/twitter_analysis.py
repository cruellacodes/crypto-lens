import os
import pandas as pd
from apify_client import ApifyClient
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from scipy.special import softmax
from utils import preprocess_tweet, is_relevant_tweet
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get the API token from the environment
api_token = os.getenv("APIFY_API_TOKEN")
if not api_token:
    raise ValueError("Apify API token not found in environment variables!")

# Initialize the Apify client with the token
client = ApifyClient(api_token)

# Load FinBERT model and tokenizer
tokenizer = AutoTokenizer.from_pretrained("ProsusAI/finbert")
model = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert")


def calculate_bullishness(tweet_text):
    """Analyze sentiment and calculate bullishness percentage using FinBERT."""
    inputs = tokenizer(tweet_text, return_tensors="pt", truncation=True, padding=True)
    outputs = model(**inputs)
    probs = softmax(outputs.logits.detach().numpy()[0])

    # Probabilities for bullish, neutral, and bearish
    bullish_prob = round(probs[0] * 100, 2)
    bearish_prob = round(probs[2] * 100, 2)
    bullishness_score = round(bullish_prob / (bullish_prob + bearish_prob), 2)
    return bullishness_score


def analyze_cashtags(cashtags):
    """Analyze sentiment for a list of cashtags."""
    data = []

    for cashtag in cashtags:
        search_term = cashtag[1:] if len(cashtag) > 6 else cashtag

        run_input = {
            "searchTerms": [search_term],
            "maxItems": 50,
            "sort": "Latest",
            "tweetLanguage": "en",
        }

        try:
            run = client.actor("61RPP7dywgiy0JPD0").call(run_input=run_input)
            bullishness_scores = []

            for item in client.dataset(run["defaultDatasetId"]).iterate_items():
                raw_tweet = item.get("text", "").strip()
                tweet_text = preprocess_tweet(raw_tweet)

                # Filter out irrelevant tweets
                if not is_relevant_tweet(tweet_text):
                    continue

                # Filter out tweets by users with less than 150 followers
                user_followers_count = item.get("userFollowersCount", 0)
                if user_followers_count < 150:
                    continue

                # Perform sentiment analysis
                score = calculate_bullishness(tweet_text)
                bullishness_scores.append(score)

            if bullishness_scores:
                avg_bullishness = round(sum(bullishness_scores) / len(bullishness_scores), 2)
                data.append({"Cashtag": cashtag, "Bullishness": avg_bullishness})
            else:
                data.append({"Cashtag": cashtag, "Bullishness": None})

        except Exception:
            data.append({"Cashtag": cashtag, "Bullishness": "Error"})

    return pd.DataFrame(data)
