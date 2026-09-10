import time
from fastapi.testclient import TestClient
from main import app
from concurrent.futures import ThreadPoolExecutor

client = TestClient(app)

# Pobierz 36 filmów
res = client.get("/api/videos?page=1")
vids = res.json().get("videos", [])
print(f"Pobrano {len(vids)} filmów do testu prędkości miniatur.")

urls = [v.get("poster_proxy") for v in vids if v.get("poster_proxy")]

start = time.time()
def fetch_thumb(u):
    r = client.get(u)
    return r.status_code, len(r.content)

with ThreadPoolExecutor(max_workers=20) as ex:
    results = list(ex.map(fetch_thumb, urls))

elapsed = time.time() - start
success_count = sum(1 for status, size in results if status == 200 and size > 100)
print(f"Pobrano {len(results)} miniatur w czasie {elapsed:.2f}s! ({success_count}/{len(results)} sukcesów)")
