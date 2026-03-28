import time
import urllib.request
import sys

print("Polling API for 30 seconds...")
start = time.time()
while time.time() - start < 30:
    try:
        with urllib.request.urlopen("http://localhost:5000/api/codes") as response:
            if response.getcode() == 200:
                print("SUCCESS: Connected to API!")
                sys.exit(0)
    except Exception as e:
        time.sleep(1)

print("FAILURE: Could not connect after 30 seconds.")
sys.exit(1)
