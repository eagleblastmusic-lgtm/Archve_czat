"""Shared readiness contract for the browser and Desktop launchers."""
import json
import time
import urllib.request


def wait_for_runtime(url="http://127.0.0.1:8000", timeout=30, expected_pid=None, stopped=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not (stopped and stopped.is_set()):
        try:
            with urllib.request.urlopen(f"{url.rstrip('/')}/api/runtime/v43", timeout=1) as response:
                marker = json.load(response)
            if (marker.get("runtime") == "v4.3-fast2" and marker.get("grouped_fast_path_v2") is True and
                    (expected_pid is None or marker.get("process_id") == expected_pid)):
                return True
        except (OSError, ValueError):
            pass
        if stopped:
            stopped.wait(.15)
        else:
            time.sleep(.15)
    return False
