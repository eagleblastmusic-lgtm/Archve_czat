from fastapi.testclient import TestClient
from main import app
import json

client = TestClient(app)

res = client.get("/api/videos?page=1")
data = res.json()
vids = data.get("videos", [])
print(f"Total videos: {len(vids)}")
if vids:
    first_vid = vids[0]
    print("First video:")
    print("  ID:", first_vid.get("id"))
    print("  Username:", first_vid.get("username"))
    print("  Thumbnail:", first_vid.get("thumbnail"))
    print("  Poster Proxy:", first_vid.get("poster_proxy"))
    print("  Preview Proxy:", first_vid.get("preview_proxy"))

    # Test fetching poster proxy
    proxy_url = first_vid.get("poster_proxy")
    if proxy_url:
        resp = client.get(proxy_url)
        print(f"Proxy GET response: status={resp.status_code}, content-type={resp.headers.get('content-type')}, len={len(resp.content)}")
        if resp.status_code != 200 or len(resp.content) < 100:
            print("Content:", resp.content[:200])
