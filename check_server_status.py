import requests
import json

try:
    print("Testing Backend Health...")
    r = requests.get("http://localhost:5000/")
    print(f"Root Status: {r.status_code}")
    print(f"Root Content: {r.text}")

    print("\nTesting Code Generation API...")
    payload = {"value": 10000, "quantity": 1, "duration": 60}
    r = requests.post("http://localhost:5000/api/codes/generate", json=payload)
    print(f"API Status: {r.status_code}")
    print(f"API Content: {r.text}")

except Exception as e:
    print(f"Connection Failed: {e}")
