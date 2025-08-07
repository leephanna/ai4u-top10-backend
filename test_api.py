import os
import requests
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Get API key
api_key = os.environ.get('RAINFOREST_API_KEY')
print(f"API Key found: {'Yes' if api_key else 'No'}")

# Test a simple API call
if api_key:
    try:
        params = {
            "api_key": api_key,
            "type": "search",
            "amazon_domain": "amazon.com",
            "search_term": "organic snacks",
            "sort_by": "featured"
        }
        
        response = requests.get("https://api.rainforestapi.com/request", params=params)
        response.raise_for_status()
        
        data = response.json()
        if "search_results" in data and len(data["search_results"]) > 0:
            print(f"API call successful! Found {len(data['search_results'])} products")
            print(f"First product: {data['search_results'][0]['title']}")
        else:
            print(f"API call succeeded but no products found. Response: {data}")
    except Exception as e:
        print(f"API call failed: {str(e)}")
else:
    print("Cannot test API - key not found in environment")