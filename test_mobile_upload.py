import requests
import json
import os

API_URL = "http://localhost:5000/api/upload/mobile"

# Tạo ảnh test giả
with open("test_photo.jpg", "wb") as f:
    f.write(os.urandom(1024))

try:
    with open("test_photo.jpg", "rb") as f:
        files = {'file': f}
        data = {'session_id': 'test-session-123'}
        res = requests.post(API_URL, files=files, data=data)
        
    print(f"Status: {res.status_code}")
    print(f"Response: {res.json()}")
except Exception as e:
    print(f"Error: {e}")
finally:
    if os.path.exists("test_photo.jpg"):
        os.remove("test_photo.jpg")
