import urllib.request
import json

try:
    print("Testing Backend Health...")
    with urllib.request.urlopen("http://localhost:5000/") as response:
        print(f"Root Status: {response.getcode()}")
        print(f"Root Content: {response.read().decode('utf-8')}")

    print("\nTesting Code Generation API...")
    url = "http://localhost:5000/api/codes/generate"
    data = json.dumps({"value": 10000, "quantity": 1, "duration": 60}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    
    with urllib.request.urlopen(req) as response:
        print(f"API Status: {response.getcode()}")
        print(f"API Content: {response.read().decode('utf-8')}")

except Exception as e:
    print(f"Connection Failed: {e}")
