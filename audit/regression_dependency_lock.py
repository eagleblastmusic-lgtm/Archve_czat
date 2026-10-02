"""Verify the locked runtime dependency closure for supported platforms."""
import importlib.metadata as metadata
from pathlib import Path
import subprocess
import sys

from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.utils import canonicalize_name

root = Path(__file__).resolve().parents[1]
pins = {}
for line in (root / "requirements.lock.txt").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    requirement = Requirement(line)
    assert len(requirement.specifier) == 1 and next(iter(requirement.specifier)).operator == "==", line
    pins[canonicalize_name(requirement.name)] = requirement

for platform in ("win32", "linux"):
    active = {name: req for name, req in pins.items() if not req.marker or req.marker.evaluate({"sys_platform": platform, "extra": ""})}
    for name, requirement in active.items():
        # A Linux installation deliberately has no Windows-only distributions.
        # Validate installed closure in the native job; both platform marker
        # projections are checked on every host; the native CI jobs verify the
        # actual installed closure for their OS and interpreter.
        if platform != sys.platform:
            continue
        distribution = metadata.distribution(name)
        assert distribution.version in requirement.specifier, (name, distribution.version, str(requirement))
        for raw in distribution.requires or []:
            dependency = Requirement(raw)
            if dependency.marker and not dependency.marker.evaluate({"sys_platform": platform, "extra": ""}):
                continue
            dependency_name = canonicalize_name(dependency.name)
            assert dependency_name in active, (platform, name, "unlocked dependency", raw)
            version = next(iter(active[dependency_name].specifier)).version
            assert version in dependency.specifier, (platform, raw, version)
    for name in ("pythonnet", "clr-loader", "cffi", "pycparser"):
        assert (name in active) == (platform == "win32"), (platform, name)

import imageio_ffmpeg
ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
result = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=10)
assert result.returncode == 0 and "ffmpeg version" in result.stdout, result.stderr
print(f"PASS: pinned dependency closure and Windows markers; Python {sys.version.split()[0]}; {result.stdout.splitlines()[0]}")
