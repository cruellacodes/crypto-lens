import os
from apify_client import ApifyClient
from dotenv import load_dotenv
from textblob import TextBlob
from new_pairs_tracker import main as fetch_new_pairs  # Import the tracker

# Load the API token from environment variables
load_dotenv()
api_token = os.getenv("APIFY_API_TOKEN")
if not api_token:
    raise ValueError("API token not found in environment variables!")

# Initialize the ApifyClient
client = ApifyClient(api_token)

# Step 1: Fetch new token pairs and remove duplicates
def get_new_pairs():
    # Call the tracker to fetch new token pairs
    tokens = fetch_new_pairs()  # This returns the results from the tracker
    if not tokens:
        print("No tokens fetched from the tracker.")
        return []

    # Extract cashtags and remove duplicates
    cashtags = list(set(f"${token['token_symbol']}" for token in tokens if token.get("token_symbol")))
    print(f"Found unique cashtags: {cashtags}")
    return cashtags

# Step 2: Search Twitter and Perform Sentiment Analysis
def analyze_cashtags(cashtags):
    overall_sentiments = {}

    for cashtag in cashtags:
        # Check if the symbol is longer than 6 characters
        search_term = cashtag[1:] if len(cashtag) > 7 else cashtag
        print(f"Analyzing tweets for {search_term}...")

        # Prepare Actor input for Twitter scraping
        run_input = {
            "searchTerms": [search_term],  # Use modified search term
            "maxItems": 50,
            "sort": "Latest",
            "tweetLanguage": "en",
        }

        try:
            # Run the Twitter scraper Actor
            run = client.actor("61RPP7dywgiy0JPD0").call(run_input=run_input)

            # Perform sentiment analysis on fetched tweets
            sentiment_scores = []
            for item in client.dataset(run["defaultDatasetId"]).iterate_items():
                tweet_text = item.get("text", "").strip()
                if tweet_text:
                    analysis = TextBlob(tweet_text)
                    sentiment_scores.append(analysis.sentiment.polarity)

            # Calculate overall sentiment
            if sentiment_scores:
                average_sentiment = sum(sentiment_scores) / len(sentiment_scores)
                sentiment_label = (
                    "Positive" if average_sentiment > 0 else
                    "Negative" if average_sentiment < 0 else
                    "Neutral"
                )
                overall_sentiments[cashtag] = sentiment_label
            else:
                overall_sentiments[cashtag] = "No tweets found"

        except Exception as e:
            print(f"Error analyzing tweets for {search_term}: {e}")
            overall_sentiments[cashtag] = "Error fetching tweets"

    return overall_sentiments

# Main function to fetch and analyze token pairs
def main_process():
    # Fetch new pairs
    new_cashtags = get_new_pairs()
    if not new_cashtags:
        print("No new token pairs found.")
        return

    # Analyze tweets for the new pairs
    overall_sentiments = analyze_cashtags(new_cashtags)

    # Display overall sentiment analysis
    print("\nOverall Sentiment Analysis:")
    for cashtag, sentiment in overall_sentiments.items():
        print(f"{cashtag}: {sentiment}")

if __name__ == "__main__":
    main_process()
