import socket
import sys
import time
import webbrowser
import threading
import uvicorn


def ensure_port_available(host="127.0.0.1", port=8000):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform.startswith("win") and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))
    except OSError as exc:
        raise RuntimeError(f"Port {port} jest zajęty; nie zakończono żadnego obcego procesu.") from exc
    finally:
        probe.close()


def open_browser():
    time.sleep(1.2)
    url = "http://127.0.0.1:8000"
    print(f"\n[Archivebate Browser] Otwieranie aplikacji w przeglądarce: {url}")
    webbrowser.open(url)


if __name__ == "__main__":
    print("=" * 60)
    print("   ARCHIVEBATE VIDEO BROWSER (GUI)")
    print("   Logowanie: konfiguracja z .env.local / zmiennych środowiskowych")
    print("=" * 60)
    try:
        ensure_port_available()
    except RuntimeError as exc:
        print(f"[START] {exc}")
        raise SystemExit(2)
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False, log_level="info")
