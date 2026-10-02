"""Run regressions against a sanitized temporary checkout, never user data.

Examples:
  python audit/implementation_runner.py --only "node audit/regression_loading_frontend.cjs"
  python audit/implementation_runner.py --benchmark-440k
  python audit/implementation_runner.py --full-ci
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(os.environ.get("ARCHIVEBATE_AUDIT_OUTPUT", str(ROOT / "audit" / "implementation_2026-09-24")))
OUTPUT.mkdir(parents=True, exist_ok=True)
# Keep the generated checkout outside the source tree by default.  A prior
# in-tree copy could otherwise be seen by copytree on the next run and grow
# recursively.  ARCHIVEBATE_ISOLATED_CHECKOUT is an explicit escape hatch for
# CI hosts that provide their own disposable workspace.
ISOLATED_CHECKOUT = Path(os.environ.get(
    "ARCHIVEBATE_ISOLATED_CHECKOUT",
    str(Path(tempfile.gettempdir()) / "archivebate_isolated_implementation_2026-09-24"),
))
ISOLATED_CHECKOUT.mkdir(parents=True, exist_ok=True)


def source_digest(root: Path) -> str:
    digest = hashlib.sha256()
    excluded_parts = {".git", "data", "node_modules", "__pycache__", "implementation_2026-09-24", "isolated_implementation_2026-09-24", "2026-09-24"}
    ignored_suffixes = {".db", ".sqlite", ".sqlite3", ".pyc", ".log", ".wal", ".shm"}
    paths = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in excluded_parts
                         and not name.startswith(("isolated_implementation_", "tmp", "deep_fixture_", "repair_results_")))
        paths.extend(Path(directory) / name for name in sorted(files))
    for path in sorted(paths):
        relative = path.relative_to(root)
        if any(part in excluded_parts for part in relative.parts) or any(part.startswith("tmp") for part in relative.parts):
            continue
        if not path.is_file() or path.suffix.lower() in ignored_suffixes or path.name.endswith((".db-wal", ".db-shm", ".lock")):
            continue
        if any(part.startswith("deep_fixture_") for part in relative.parts):
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def copy_project(destination: Path) -> None:
    for path in ROOT.iterdir():
        if not path.is_file() or path.name.startswith(".env"):
            continue
        if path.suffix.lower() in {".py", ".md", ".bat", ".ps1", ".txt", ".in", ".toml", ".yml", ".yaml"} or path.name == ".gitignore":
            shutil.copy2(path, destination / path.name)

    def audit_ignore(directory, names):
        ignored = {
            "2026-09-24", "__pycache__", "implementation_2026-09-24",
        }
        ignored.update(name for name in names if name.startswith(("isolated_implementation_", "repair_results_")))
        ignored.update(name for name in names if name.startswith(("tmp", "deep_fixture_")))
        ignored.update(name for name in names if name.endswith((".db", ".db-wal", ".db-shm", ".lock", ".pyc", ".log")))
        return ignored

    for name in ("static", "audit", ".github"):
        source = ROOT / name
        if source.exists():
            shutil.copytree(source, destination / name, ignore=audit_ignore, dirs_exist_ok=True)
    (destination / "data").mkdir(exist_ok=True)
    seed = ROOT / "data" / "model_tags.seed.json"
    if seed.is_file():
        shutil.copy2(seed, destination / "data" / seed.name)


def ci_commands() -> list[tuple[str, list[str]]]:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    jobs: list[tuple[str, list[str]]] = []
    current = ""
    for line in workflow.splitlines():
        match_job = re.match(r"^  ([a-z0-9-]+):\s*$", line)
        if match_job:
            current = match_job.group(1)
            continue
        match_run = re.match(r"^\s+run:\s+(.+?)\s*$", line)
        if not match_run:
            continue
        try:
            command = shlex.split(match_run.group(1), posix=True)
        except ValueError:
            continue
        if command and command[0] in {"python", "node"}:
            jobs.append((current, command))
    return jobs


def install_isolation_temp_shim(isolated: Path) -> Path:
    """Give child tests a writable, deterministic tempfile root.

    The managed Windows sandbox can create a random tempfile directory but can
    then deny SQLite/PIL writes inside it.  CI itself uses normal tempfile ACLs;
    this shim is only installed in the disposable checkout used by this local
    evidence runner, so it does not change application or production tests.
    """
    shim = isolated / "sitecustomize.py"
    shim.write_text(
        '''import itertools
import os
from pathlib import Path
import tempfile

_root = Path(os.environ.get("ARCHIVEBATE_TEST_TEMP_ROOT", Path.cwd() / "audit" / "runner_temp"))
_root.mkdir(parents=True, exist_ok=True)
_counter = itertools.count()

def _new_path(prefix="tmp", suffix=""):
    path = _root / f"{prefix}{os.getpid()}_{next(_counter)}{suffix}"
    path.mkdir(parents=True, exist_ok=False)
    return path

def _mkdtemp(suffix="", prefix="tmp", dir=None):
    del dir
    return str(_new_path(prefix, suffix))

class _PersistentTemporaryDirectory:
    def __init__(self, suffix=None, prefix=None, dir=None, ignore_cleanup_errors=False):
        del dir, ignore_cleanup_errors
        self.name = _mkdtemp(suffix or "", prefix or "tmp")
    def __enter__(self):
        return self.name
    def __exit__(self, exc_type, exc, tb):
        return False
    def cleanup(self):
        return None

tempfile.mkdtemp = _mkdtemp
tempfile.TemporaryDirectory = _PersistentTemporaryDirectory
tempfile.gettempdir = lambda: str(_root)
tempfile.tempdir = str(_root)
''',
        encoding="utf-8",
    )
    return shim


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full-ci", action="store_true", help="run every single-line Python/Node command in all CI jobs")
    parser.add_argument("--only", help="run one exact workflow command, e.g. 'node audit/regression_loading_frontend.cjs'")
    parser.add_argument("--benchmark-440k", action="store_true", help="run the isolated 440k-row grouped feed/index benchmark")
    return parser.parse_args()


def main():
    args = parse_args()
    if sum(bool(value) for value in (args.full_ci, args.only, args.benchmark_440k)) != 1:
        raise SystemExit("Choose exactly one of --full-ci, --only, or --benchmark-440k")

    if args.full_ci:
        commands = ci_commands()
    elif args.only:
        matches = [("targeted", command) for _, command in ci_commands()
                   if " ".join(command) == args.only]
        matches = matches[:1]
        if not matches:
            command = shlex.split(args.only)
            matches = [("targeted", command)]
        commands = matches
    else:
        commands = [("benchmark", ["python", "audit/regression_v43_grouped_fast.py", "--benchmark-440k"])]

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    metadata = {
        "source_head": head.stdout.strip() if head.returncode == 0 else "unavailable",
        "source_manifest_sha256": source_digest(ROOT),
        "host_python": sys.version,
        "host_sqlite": __import__("sqlite3").sqlite_version,
        "host_node": subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip(),
        "mode": "full-ci" if args.full_ci else ("benchmark-440k" if args.benchmark_440k else "targeted"),
        "tests": [],
    }

    isolated = ISOLATED_CHECKOUT
    allowed_checkout_roots = (ROOT.resolve(), Path(tempfile.gettempdir()).resolve())
    isolated_resolved = isolated.resolve()
    if not any(isolated_resolved.is_relative_to(root) for root in allowed_checkout_roots):
        raise RuntimeError(
            f"Refusing to create test checkout outside {ROOT} or {Path(tempfile.gettempdir())}"
        )
    try:
        copy_project(isolated)
        environment = dict(os.environ)
        for key in list(environment):
            if key.startswith("ARCHIVEBATE_") or key in {"PYTHONPATH", "PYTHONHOME"}:
                environment.pop(key, None)
        environment.update({
            "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
            "ARCHIVEBATE_AUDIT_ISOLATED": "1",
        })
        isolated_tmp = isolated / "tmp"
        isolated_tmp.mkdir(parents=True, exist_ok=True)
        isolated_data = isolated / "data"
        isolated_data.mkdir(parents=True, exist_ok=True)
        isolated_temp_root = isolated / "audit" / "runner_temp"
        isolated_temp_root.mkdir(parents=True, exist_ok=True)
        install_isolation_temp_shim(isolated)
        environment.update({
            "TEMP": str(isolated_tmp), "TMP": str(isolated_tmp), "TMPDIR": str(isolated_tmp),
            "ARCHIVEBATE_USER_STORE": str(isolated_data / "audit_user_store.json"),
            "ARCHIVEBATE_CATALOG_DB": str(isolated_data / "audit_catalog.db"),
            "ARCHIVEBATE_TEST_TEMP_ROOT": str(isolated_temp_root),
            "PYTHONPATH": str(isolated),
        })
        metadata["isolation"] = "temporary source copy; user data, credentials, and git metadata excluded"
        metadata["commands_source"] = ".github/workflows/ci.yml" if args.full_ci or args.only else "explicit 440k benchmark"

        for job, command in commands:
            executable = sys.executable if command[0] == "python" else command[0]
            actual = [executable, *command[1:]]
            display = " ".join(command)
            slug = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{job}_{display}").strip("_")[:120]
            started = time.perf_counter()
            try:
                result = subprocess.run(
                    actual, cwd=isolated, env=environment,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", timeout=240,
                )
                exit_code, output = result.returncode, result.stdout
            except subprocess.TimeoutExpired as exc:
                exit_code = 124
                output = "TIMEOUT after 240 seconds\n" + str(exc.stdout or "")
            elapsed = round(time.perf_counter() - started, 3)
            log_name = f"{len(metadata['tests']) + 1:03d}_{slug}.log"
            (OUTPUT / log_name).write_text(output, encoding="utf-8")
            row = {
                "job": job, "command": display, "exit_code": exit_code,
                "seconds": elapsed, "log": log_name,
            }
            metadata["tests"].append(row)
            (OUTPUT / "test_results.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{exit_code:3} {elapsed:8.2f}s [{job}] {display}", flush=True)

    finally:
        # The checkout is intentionally retained as a reviewable, data-free
        # fixture; only the generated test logs are in OUTPUT.
        pass

    failures = [row for row in metadata["tests"] if row["exit_code"] != 0]
    print(f"RESULTS={OUTPUT / 'test_results.json'}")
    print(f"SUMMARY={len(metadata['tests']) - len(failures)}/{len(metadata['tests'])} passed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
