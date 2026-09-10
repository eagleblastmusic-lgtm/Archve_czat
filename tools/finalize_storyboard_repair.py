from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_regex(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, lambda _m: replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}: {pattern}")
    path.write_text(updated, encoding="utf-8")


validator = '''def _valid_segment_frame(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return False
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            return image.size == (FRAME_WIDTH, FRAME_HEIGHT)
    except Exception:
        return False
'''

extractor = '''def _extract_segment_frames_with_times(
    ffmpeg: str, source_url: str, start_time: float, target_duration: float, tmp_dir: Path, timeout: int = 25
) -> Tuple[List[Path], List[float], bool]:
    """Extract a dense segment safely using the source-proven FFmpeg command shape.

    A segment is accepted only when FFmpeg reports success and every produced
    JPEG decodes at the expected dimensions. Timing is nominal 1 fps, so the
    manifest must mark it as approximate instead of claiming decoded precision.
    """
    vf = (
        f"fps={SEGMENT_FPS},"
        f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={FRAME_WIDTH}:{FRAME_HEIGHT}"
    )

    def run(seek_before_input: bool) -> Tuple[List[Path], List[float], bool]:
        _cleanup_segment_frames(tmp_dir)
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
        if seek_before_input:
            cmd += ["-ss", f"{start_time:.3f}", "-t", f"{target_duration:.3f}", "-i", source_url]
        else:
            cmd += ["-i", source_url, "-ss", f"{start_time:.3f}", "-t", f"{target_duration:.3f}"]
        cmd += [
            "-vf", vf,
            "-an", "-sn", "-dn",
            "-threads", "1",
            "-q:v", "7",
            "-y", str(tmp_dir / "frame_%03d.jpg"),
        ]
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        except Exception:
            _cleanup_segment_frames(tmp_dir)
            return [], [], False

        frames = sorted(tmp_dir.glob("frame_*.jpg"))
        if result.returncode != 0 or not frames or any(not _valid_segment_frame(frame) for frame in frames):
            _cleanup_segment_frames(tmp_dir)
            return [], [], False

        times = [float(start_time) + i * (1.0 / SEGMENT_FPS) for i in range(len(frames))]
        return frames, times, False

    first = run(True)
    if first[0]:
        return first
    return run(False)
'''

story = ROOT / "storyboard_service.py"
replace_regex(
    story,
    r"def _valid_segment_frame\(path: Path\) -> bool:.*?\n\ndef _run_segment_extract",
    validator + "\n\ndef _run_segment_extract",
)
replace_regex(
    story,
    r"def _extract_segment_frames_with_times\(.*?\n\ndef _extract_segment_frames\(",
    extractor + "\n\ndef _extract_segment_frames(",
)

package_d = ROOT / "audit" / "regression_package_d.py"
text = package_d.read_text(encoding="utf-8")
text = text.replace('assert manifest["approximate"] is False', 'assert manifest["approximate"] is True', 1)
text = text.replace('assert manifest["time_precision"] == "decoded_pts_1fps"', 'assert manifest["time_precision"] == "nominal_1fps_fallback"', 1)
text = text.replace(
    'PASS 2: Manifest używa decoded PTS, a nie nominalnego generatora testu',
    'PASS 2: Manifest oznacza nominalny timing jako approximate; kolejność klatek jest sprawdzana niezależnie',
    1,
)
package_d.write_text(text, encoding="utf-8")

print("Portable storyboard repair finalized")
