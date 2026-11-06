import os
import requests
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GROQ_API_KEY")

headers = {"Authorization": f"Bearer {api_key}"}
response = requests.get("https://api.groq.com/openai/v1/models", headers=headers)

print("Available Models for your API Key:")
if response.status_code == 200:
    for model in response.json().get("data", []):
        print(f"- {model['id']}")
else:
    print("Error fetching models:", response.text)