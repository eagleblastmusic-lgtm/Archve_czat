import os
import socket
import threading
import time
import urllib.request

import uvicorn
import webview

os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--autoplay-policy=no-user-gesture-required"


def ensure_port_available(host="127.0.0.1", port=8000):
    """Fail safely on a bind conflict. Never terminate an unowned process."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))
    except OSError as exc:
        raise RuntimeError(
            f"Port {port} jest zajęty. Zamknij poprzednią instancję aplikacji lub proces używający portu; nic nie zostało automatycznie zakończone."
        ) from exc
    finally:
        probe.close()


def start_server():
    uvicorn.run("runtime_app:app", host="127.0.0.1", port=8000, reload=False, log_level="warning")


def wait_for_server(url="http://127.0.0.1:8000", timeout=10):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def install_timeline_diagnostics(app, window):
    """Read the actual Desktop DOM; never include provider URLs or credentials."""
    @app.get("/api/runtime/desktop/timeline")
    def desktop_timeline_snapshot():
        return window.evaluate_js("""(() => {
            const v = document.getElementById('modalVideo');
            const t = document.getElementById('modalTimelineTooltip');
            const s = document.getElementById('modalTimelineSprite');
            const label = document.getElementById('modalTimelinePreviewStatus');
            const state = window.ArchivebateAppContext?.state;
            const visible = e => !!e && getComputedStyle(e).display !== 'none';
            const image = s?.querySelector('img');
            return {
                video_id: state?.currentVideoId || '',
                metadata_duration: state?.currentVideoDetails?.duration,
                duration: Number.isFinite(v?.duration) ? v.duration : null,
                paused: v?.paused,
                tooltip_visible: visible(t),
                tooltip_time: document.getElementById('modalTimelineTimeText')?.innerText,
                sprite_visible: visible(s),
                frame_time: s?.dataset.frameTime,
                image_loaded: image?.complete && image?.naturalWidth > 0,
                status_visible: visible(label),
                status: label?.textContent,
                client: window.ArchivebateYouTubeStoryboard?.stats(),
                coordinator: window.ArchivebateV43TimelineFallback?.stats()
            };
        })()""")


if __name__ == "__main__":
    print("=" * 60)
    print("   ARCHIVEBATE & CAMWHORES PRO (Aplikacja Pulpitowa)")
    print("   Uruchamianie natywnego okna bez przeglądarki...")
    print("=" * 60)
    try:
        ensure_port_available()
    except RuntimeError as exc:
        print(f"[START] {exc}")
        raise SystemExit(2)

    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    if not wait_for_server():
        print("[START] Serwer nie zgłosił gotowości w wymaganym czasie.")
        raise SystemExit(3)

    window = webview.create_window(
        title="Archivebate & Camwhores Desktop",
        url="http://127.0.0.1:8000",
        width=1440,
        height=920,
        min_size=(960, 640),
        background_color="#0a0e17"
    )
    from runtime_app import app
    install_timeline_diagnostics(app, window)
    webview.start(private_mode=False)
