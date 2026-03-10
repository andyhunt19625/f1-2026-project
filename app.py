import requests
from pymongo import MongoClient

# 1. Setup Database Connection
client = MongoClient("mongodb://localhost:27017/")
db = client['f1_2026']
collection = db['melbourne_race']

def fetch_2026_data():
    print("Connecting to OpenF1 API for 2026 Melbourne Data...")
    session_key = 9654 
    url = f"https://api.openf1.org/v1/laps?session_key={session_key}"
    
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            print(f"Success! Retrieved {len(data)} lap records.")
            if data:
                collection.insert_many(data)
                print("Data successfully saved to MongoDB.")
        else:
            print(f"Failed to fetch data. Status code: {response.status_code}")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    print("--- Starting 2026 Australian GP Data Ingest ---")
    fetch_2026_data()
    print("--- Ingest Complete ---")
