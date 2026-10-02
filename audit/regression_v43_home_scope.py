"""Release gate for home loading behavior; deliberately no historical blob hashes.

The executable home-loading test owns the behavioral assertions for lazy first
paint, current-view warmup, feed retry, revision tracking, and cache invalidation.
This wrapper keeps the existing CI entry point while invoking that test once.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
completed = subprocess.run(
    ["node", str(ROOT / "audit" / "regression_home_loading.cjs")],
    cwd=ROOT,
    check=False,
)
if completed.returncode:
    raise SystemExit(completed.returncode)
print("PASS: home scope is covered by current behavioral loading contracts")
