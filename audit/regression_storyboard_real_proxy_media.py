"""End-to-end FFmpeg storyboard ingress via the actual local FastAPI video proxy.

Regression for real media served by CDNs with misleading/absent Content-Type.
Uses a generated MP4 fixture and mocks only the *external* CDN, not the
localhost streaming server, FFmpeg, request parsing or JPEG extraction.
Does not access login credentials, network CDNs, or user profile data.
"""
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import imageio_ffmpeg
import uvicorn

import main
import runtime_app
import timeline_storyboard as sb

VIDEO_URL = "https://media.example.invalid/test.mp4"


class Upstream:
    def __init__(self, blob, content_type=None, status_code=200, extra=None):
        self.status_code = status_code
        self.headers = {
            **({"Content-Type": content_type} if content_type is not None else {}),
            **(extra or {}),
        }
        self.blob = blob
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for offset in range(0, len(self.blob), chunk_size):
            yield self.blob[offset:offset + chunk_size]

    def close(self):
        self.closed = True


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_movie(out_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
         "-t", "3", "-c:v", "mpeg4", "-q:v", "8",
         "-y", str(out_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=20,
    )
    return out_path.read_bytes()


def test_magic_detection():
    assert main._sniff_video_magic(b"\x00\x00\x00\x14ftypisom0000") == "video/mp4"
    assert main._sniff_video_magic(bytes.fromhex("1a45dfa3") + b"other") == "video/webm"
    assert main._sniff_video_magic(b"FLV\x01" + b"\x00" * 16) == "video/x-flv"
    for payload in (b"<html><script>alert(1)</script>", b"console.log(1)", b"hello", b""):
        assert main._sniff_video_magic(payload) is None


def test_local_ffmpeg_decodes_mislabelled_video():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        blob = make_movie(root / "video.mp4")
        assert main._sniff_video_magic(blob) == "video/mp4"

        port = free_port()
        old_port = os.environ.get("PORT")
        os.environ["PORT"] = str(port)
        served = []
        mime = {"value": "text/plain"}

        def upstream_get(http_session, url, **kwargs):
            assert url == VIDEO_URL, url
            # FFmpeg needs byte-range seeking to find an MP4's moov atom.
            # Return real RFC 9110 byte ranges, not a broken 200 full body
            # in response to every Range request.
            import re
            headers = kwargs.get("headers") or {}
            header = headers.get("Range") or headers.get("range") or ""
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", header)
            code = 200
            data = blob
            extra = {"Content-Length": str(len(blob)), "Accept-Ranges": "bytes"}
            if match:
                start = int(match.group(1))
                end = min(len(blob) - 1, int(match.group(2))) if match.group(2) else len(blob) - 1
                if start >= len(blob):
                    code = 416
                    data = b""
                    extra = {"Content-Range": f"bytes */{len(blob)}"}
                else:
                    code = 206
                    data = blob[start:end + 1]
                    extra = {
                        "Content-Length": str(len(data)),
                        "Content-Range": f"bytes {start}-{end}/{len(blob)}",
                        "Accept-Ranges": "bytes",
                    }
            item = Upstream(data, content_type=mime["value"], status_code=code,
                            extra=extra)
            served.append(item)
            return item

        config = uvicorn.Config(runtime_app.app, host="127.0.0.1", port=port,
                                log_level="critical", access_log=False, lifespan="off")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        try:
            with patch.object(main, "is_safe_remote_url", return_value=True), \
                 patch.object(main, "_validated_session_get", side_effect=upstream_get), \
                 patch.object(main, "_is_host_failed", return_value=(False, "")):
                thread.start()
                for _ in range(150):
                    if server.started:
                        break
                    time.sleep(0.05)
                assert server.started, "Local app server did not start"

                for content_type in ("text/plain", None, "application/octet-stream"):
                    mime["value"] = content_type
                    with main._MEDIA_SNIFF_LOCK:
                        main._MEDIA_SNIFF_CACHE.clear()
                    jpg = root / ("frame_" + str(content_type or "missing").replace("/", "_") + ".jpg")
                    ok = sb._default_extract_frame(
                        f"http://127.0.0.1:{port}/api/video/stream?url={VIDEO_URL}&owner=storyboard",
                        0.4, jpg, timeout=12, fast=False,
                        video_id="audit_proxy_fixture", source="archivebate"
                    )
                    if not ok:
                        import requests
                        url = f"http://127.0.0.1:{port}/api/video/stream?url={VIDEO_URL}&owner=storyboard"
                        try:
                            check = requests.get(url, timeout=10)
                            print("DEBUG proxy:", check.status_code,
                                  check.headers.get("content-type"),
                                  check.headers.get("content-length"),
                                  len(check.content), check.content[:24].hex(),
                                  flush=True)
                        except Exception as exc:
                            print("DEBUG proxy error:", repr(exc), flush=True)
                        cmd = [
                            imageio_ffmpeg.get_ffmpeg_exe(),
                            "-hide_banner", "-loglevel", "error", "-nostdin",
                            "-ss", "0.400", "-seekable", "1",
                            "-probesize", "262144", "-analyzeduration", "1000000",
                            "-reconnect", "1", "-reconnect_streamed", "1",
                            "-reconnect_delay_max", "2",
                            "-protocol_whitelist", "http,https,tcp,tls",
                            "-i", url, "-map", "0:v:0", "-frames:v", "1",
                            "-an", "-sn", "-dn", "-threads", "1",
                            "-vf", "scale=160:90:force_original_aspect_ratio=increase,crop=160:90",
                            "-q:v", "8", "-y", str(root / "debug.jpg")
                        ]
                        diag = subprocess.run(cmd, stdout=subprocess.PIPE,
                                              stderr=subprocess.PIPE, timeout=12)
                        print("DEBUG FFmpeg:", diag.returncode,
                              diag.stderr.decode("utf-8", "replace")[-2000:], flush=True)
                    assert ok and jpg.is_file() and jpg.stat().st_size > 350, (
                        f"Storyboard FFmpeg failed with Content-Type {content_type!r}")
                assert served and all(item.closed for item in served)
        finally:
            server.should_exit = True
            if thread.is_alive():
                thread.join(timeout=7)
            if old_port is None:
                os.environ.pop("PORT", None)
            else:
                os.environ["PORT"] = old_port
            assert not thread.is_alive(), "Local test HTTP server failed to shut down"


if __name__ == "__main__":
    test_magic_detection()
    print("PASS: safe media signatures vs HTML/JavaScript", flush=True)
    test_local_ffmpeg_decodes_mislabelled_video()
    print("PASS: FFmpeg extracts real JPEG through local proxy with text/plain / missing / octet-stream MIME", flush=True)
