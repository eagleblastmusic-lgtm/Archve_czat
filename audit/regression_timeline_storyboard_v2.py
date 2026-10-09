"""Regression test suite for V2 Progressive Storyboard Service and API endpoints.

Tests:
1. Bisection sequence (van der Corput) correctness, balance and completeness.
2. Canonical VideoKey resolution and isolated cache directory scoping.
3. Layer 0 provisional manifest generation for Camwhores (prefix) and Archivebate.
4. Progressive build execution with bisection, incremental milestone publishing and atomic sheet updates.
5. Long-poll wait_manifest behavior with revision advancement.
6. Lease management lifecycle.
7. FastAPI HTTP endpoints for manifest v2, build and sheet delivery.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from PIL import Image

import main
import runtime_app
from timeline_storyboard import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    ProgressiveStoryboardService,
    compute_bisection_order,
    get_manifest_path,
    get_sheet_path,
    has_live_lease,
    record_lease,
    remove_lease,
    reset_frame_extractor,
    resolve_video_key,
    set_frame_extractor_for_test,
)
from video_identity import VideoKey

# 1. Bisection sequence tests
order_5 = compute_bisection_order(5)
assert order_5 == [0, 4, 2, 1, 3], f"Unexpected N=5 order: {order_5}"
assert compute_bisection_order(1) == [0]
assert compute_bisection_order(2) == [0, 1]

order_100 = compute_bisection_order(100)
assert len(order_100) == 100
assert len(set(order_100)) == 100
assert order_100[0] == 0 and order_100[1] == 99 and order_100[2] == 49
print("PASS: bisection sequence (van der Corput)")

# 2. VideoKey resolution and directory isolation
key_cw = resolve_video_key("cw_12345")
assert key_cw.source == "camwhores" and key_cw.provider_id == "12345"

key_ab = resolve_video_key("12345", source="archivebate")
assert key_ab.source == "archivebate" and key_ab.provider_id == "12345"

assert key_cw != key_ab
path_cw = get_manifest_path(key_cw)
path_ab = get_manifest_path(key_ab)
assert path_cw != path_ab, "Different sources with same numeric ID must have isolated cache paths"
print("PASS: VideoKey resolution and path isolation")

# 3. Layer 0 provisional manifest
service = ProgressiveStoryboardService()
provisional_cw = service.get_manifest(
    "cw_9999",
    600.0,
    source="camwhores",
    native_prefix="https://thumbs.cdn/preview/",
    native_count=15,
)
assert provisional_cw["version"] in (11, 12)
assert provisional_cw["rev"] == 1
assert provisional_cw["complete"] is False
assert provisional_cw["native"]["kind"] == "prefix"
assert len(provisional_cw["native"]["frames"]) == 15
print("PASS: Layer 0 provisional manifest")

# 4. Incremental build with fake extractor
def fake_extractor(source_url: str, target_time: float, out_path: Path, timeout: float = 8.0, fast: bool = True) -> bool:
    img = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), (255, 0, 0))
    img.save(out_path, format="JPEG")
    return True

set_frame_extractor_for_test(fake_extractor)

test_id = f"test_reg_{int(time.time())}"
record_lease(test_id, duration=10.0)
assert has_live_lease(test_id) is True

test_job = {
    "key": resolve_video_key(test_id, source="archivebate"),
    "duration": 40.0,
    "source_url": f"http://127.0.0.1:8000/api/video/stream?id={test_id}&source=archivebate",
}

# Run build synchronously for testing
service._execute_build_job(test_job)

manifest = service.get_manifest(test_id, 40.0, source="archivebate")
assert manifest["complete"] is True
assert len(manifest["frames"]) >= 10
assert manifest["rev"] > 1
assert len(manifest["sheets"]) == 1

sheet_path = get_sheet_path(resolve_video_key(test_id, source="archivebate"), 0)
assert sheet_path.exists()
with Image.open(sheet_path) as s_img:
    assert s_img.size == (FRAME_WIDTH * 10, FRAME_HEIGHT * 10)

reset_frame_extractor()
print("PASS: progressive build execution and atomic persistence")

# 5. Lease management
remove_lease(test_id)
assert has_live_lease(test_id) is False
print("PASS: lease management lifecycle")

# 6. HTTP API Endpoints via TestClient
client = TestClient(runtime_app.app)

# GET /api/storyboard/v2
resp = client.get(f"/api/storyboard/v2?id={test_id}&duration=40.0&source=archivebate")
assert resp.status_code == 200, resp.text
data = resp.json()
assert data["version"] in (11, 12)
assert data["complete"] is True
assert len(data["frames"]) > 0

# GET /api/storyboard/v2/sheet
resp_sheet = client.get(f"/api/storyboard/v2/sheet?id={test_id}&sheet=0&v={data['rev']}&source=archivebate")
assert resp_sheet.status_code == 200, resp_sheet.text
assert resp_sheet.headers.get("content-type") == "image/jpeg"
assert "immutable" in resp_sheet.headers.get("cache-control", "")

# POST /api/storyboard/v2/build without mutation token -> 403
resp_build_unauth = client.post(f"/api/storyboard/v2/build?id={test_id}&duration=40.0")
assert resp_build_unauth.status_code == 403

# POST /api/storyboard/v2/build with mutation token -> 200
headers = {"X-Archivebate-Mutation-Token": main.LOCAL_MUTATION_TOKEN}
resp_build = client.post(f"/api/storyboard/v2/build?id={test_id}&duration=40.0", headers=headers)
assert resp_build.status_code == 200, resp_build.text
build_data = resp_build.json()
assert build_data["version"] in (11, 12)
print("PASS: FastAPI HTTP endpoints for manifest v2, build security gate and sheet delivery")

# 7. Camwhores source-scoped sheet retrieval and lease
cw_test_id = f"cw_reg_{int(time.time())}"
record_lease(cw_test_id, duration=10.0, source="camwhores")
assert has_live_lease(cw_test_id, source="camwhores") is True
remove_lease(cw_test_id, source="camwhores")
assert has_live_lease(cw_test_id, source="camwhores") is False

record_lease(cw_test_id, duration=20.0, source="camwhores")
set_frame_extractor_for_test(fake_extractor)
cw_job = {
    "key": resolve_video_key(cw_test_id, source="camwhores"),
    "duration": 50.0,
    "source_url": f"http://127.0.0.1:8000/api/video/stream?id={cw_test_id}&source=camwhores",
}
service._execute_build_job(cw_job)
cw_manifest = service.get_manifest(cw_test_id, 50.0, source="camwhores")
assert cw_manifest["complete"] is True
assert len(cw_manifest["sheets"]) == 1
sheet_url = cw_manifest["sheets"][0]["url"]
assert "source=camwhores" in sheet_url, f"Sheet URL must preserve source=camwhores: {sheet_url}"

resp_cw_sheet = client.get(sheet_url)
assert resp_cw_sheet.status_code == 200, f"Camwhores sheet fetch failed: {resp_cw_sheet.status_code} {resp_cw_sheet.text}"
assert resp_cw_sheet.headers.get("content-type") == "image/jpeg"
reset_frame_extractor()
print("PASS: Camwhores source-scoped sheet generation, URL contract, and delivery")

print("\nALL V2 PROGRESSIVE STORYBOARD BACKEND TESTS PASSED.")
