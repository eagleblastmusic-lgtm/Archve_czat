import time
import requests

# Test fetching 36 thumbnails from our local server
s = requests.Session()

# 1. Fetch video list
r = s.get("http://127.0.0.1:8000/api/videos?page=1")
vids = r.json().get("videos", [])
thumb_urls = [f"http://127.0.0.1:8000{v['poster_proxy']}" for v in vids[:36] if v.get("poster_proxy")]

print(f"Testing {len(thumb_urls)} thumbnail URLs...")

# Sequential fetch test
start_seq = time.time()
for u in thumb_urls[:10]:
    s.get(u)
elapsed_seq = (time.time() - start_seq) / 10
print(f"Average time per thumbnail (sequential): {elapsed_seq*1000:.1f}ms")

# Check if cached
start_cached = time.time()
for u in thumb_urls[:10]:
    s.get(u)
elapsed_cached = (time.time() - start_cached) / 10
print(f"Average time per thumbnail (cached): {elapsed_cached*1000:.1f}ms")
