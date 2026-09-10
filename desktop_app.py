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
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False, log_level="warning")


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

    webview.create_window(
        title="Archivebate & Camwhores Desktop",
        url="http://127.0.0.1:8000",
        width=1440,
        height=920,
        min_size=(960, 640),
        background_color="#0a0e17"
    )
    webview.start(private_mode=False)
