import hashlib
from pathlib import Path

# V4.3 is a playback/timeline release. These home-page files are frozen to the
# exact V4.2/master blobs. Any change here is a release-scope violation unless
# it is deliberately reviewed as a separate home-page change.
EXPECTED_BLOBS = {
    "main.py": "1a706e500729242be0e311b8dc9c3e2e07c442c7",
    "static/app.js": "c84b031c2e2e5d3f38d8b025f20b111ffbda3a0e",
    "static/api-client.js": "689ce3f85e28aca791772c00b59165f6847cf06e",
    "static/index.html": "5f0ea6f055db2d977ac9e575cbdb7a7ef681cc20",
    "static/video-card.js": "aa4bbd2bfa75368a17f0cade611bd77e072af0c7",
    "static/video-grid.js": "ef5e35a5521d8018e6bdd32a3d317028156a5161",
    "static/video-prefetch.js": "633eb9e1c73d85cdc7e47bbf450e651d4dd9e03d",
    "static/video-views.js": "ab7f9f2fc801006e4d3da4b336cce653588edf91",
}


def git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


mismatches = []
for filename, expected in EXPECTED_BLOBS.items():
    path = Path(filename)
    if not path.is_file():
        mismatches.append(f"{filename}: missing")
        continue
    actual = git_blob_sha(path)
    if actual != expected:
        mismatches.append(f"{filename}: expected {expected}, got {actual}")

if mismatches:
    raise SystemExit("V4.3 home-scope violation:\n" + "\n".join(mismatches))

print(f"PASS V4.3 HOME SCOPE: {len(EXPECTED_BLOBS)} home/feed/grid files are byte-identical to V4.2 master")
