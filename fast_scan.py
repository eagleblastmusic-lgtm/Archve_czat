import os
import re
import json
import time
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

from model_tags import model_tag_manager
from client import ArchivebateSession
from config import get_archivebate_credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fast_scan")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
}

TAG_CATEGORIES = [
    ("trans", ["trans", "shemale", "ts", "ladyboy", "tranny"]),
    ("teen", ["teen", "college", "young", "petite", "18yo", "19yo"]),
    ("milf", ["milf", "mature", "mom"]),
    ("anal", ["anal", "butt", "ass"]),
    ("squirt", ["squirt", "gush"]),
    ("ebony", ["ebony", "black"]),
    ("latina", ["latina", "colombian", "venezuelan", "brazilian", "mexican"]),
    ("asian", ["asian", "japanese", "korean", "thai"]),
    ("bbw", ["bbw", "curvy", "thick", "chubby"]),
    ("couple", ["couple", "duo", "mf", "threesome"]),
    ("feet", ["feet", "foot", "toes", "soles", "stockings"]),
    ("dildo", ["dildo", "strap"]),
    ("cosplay", ["cosplay", "anime"]),
    ("bigboobs", ["bigboobs", "boobs", "tits"])
]

def scan_user_store(cancel_event=None):
    """1. Skanuje wszystkie profile z bazy użytkownika (ulubione, historia, obserwowani)."""
    user_store_file = os.path.abspath(
        os.getenv("ARCHIVEBATE_USER_STORE")
        or os.path.join(os.path.dirname(__file__), "data", "user_store.json")
    )
    if not os.path.exists(user_store_file):
        return
    try:
        with open(user_store_file, "r", encoding="utf-8") as f:
            d = json.load(f)
        models = set()
        for k in ['favorites', 'history', 'following']:
            for v in d.get(k, []):
                u = v.get('username')
                if u:
                    models.add(u)
        logger.info(f"[Scanner] Rozpoczynam skanowanie {len(models)} profili z konta użytkownika...")
        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = [executor.submit(model_tag_manager.resolve_model, model) for model in models]
            for future in as_completed(futures):
                if cancel_event and cancel_event.is_set():
                    break
                future.result()
        logger.info(f"[Scanner] Zakończono skanowanie profili użytkownika.")
    except Exception as e:
        logger.warning(f"[Scanner] Błąd skanowania user_store: {e}")

def scan_camwhores_tags(cancel_event=None):
    """2. Skanuje główne kategorie Camwhores i przypisuje tagi do modelek (głębokie skanowanie dla Trans)."""
    logger.info("[Scanner] Skanowanie kategorii Camwhores.tv...")
    for tag_name, _ in TAG_CATEGORIES:
        if cancel_event and cancel_event.is_set():
            return
        max_p = 25 if tag_name == "trans" else 3
        count = 0
        for p in range(1, max_p + 1):
            if cancel_event and cancel_event.is_set():
                return
            url = f"https://www.camwhores.tv/tags/{tag_name}/{p}/" if p > 1 else f"https://www.camwhores.tv/tags/{tag_name}/"
            try:
                r = requests.get(url, headers=HEADERS, timeout=6)
                if r.status_code == 200:
                    titles = re.findall(r'title=["\']([^"\']+)["\']', r.text)
                    for t in titles:
                        words = t.split()
                        if words:
                            w0 = words[0].strip('-_.,')
                            if re.match(r'^[a-zA-Z0-9_]{3,22}$', w0) and w0.lower() not in ['the', 'hot', 'new', 'best', 'video', 'cam', 'watch', 'add', 'porn', 'stripchat', 'chaturbate']:
                                from storage import storage
                                if storage.is_model_blocked(w0):
                                    continue
                                g = tag_name.capitalize() if tag_name in ["trans", "couple"] else None
                                model_tag_manager.set_model(w0, gender=g, tags=[tag_name.capitalize()])
                                count += 1
            except Exception as e:
                pass
        logger.info(f"[Scanner] Kategoria #{tag_name}: zaindeksowano {count} modelek z {max_p} stron.")

def scan_camwhores_popular_models(max_pages=20, cancel_event=None):
    """3. Skanuje popularne modelki z Camwhores i ich tagi."""
    logger.info(f"[Scanner] Skanowanie {max_pages} stron najpopularniejszych modelek Camwhores...")
    def _scan_model_page(m_name):
        try:
            from storage import storage
            if storage.is_model_blocked(m_name):
                return
            r = requests.get(f"https://www.camwhores.tv/models/{m_name}/", headers=HEADERS, timeout=4)
            if r.status_code == 200:
                cw_tags = set()
                for ct in re.findall(r'/tags/([^/"]+)/', r.text):
                    ct_clean = ct.strip().lower()
                    for t_name, kws in TAG_CATEGORIES:
                        if ct_clean in kws or ct_clean == t_name:
                            cw_tags.add(t_name.capitalize())
                gender = "Trans" if "Trans" in cw_tags else None
                model_tag_manager.set_model(m_name, gender=gender, tags=list(cw_tags))
        except Exception:
            pass

    for p in range(1, max_pages + 1):
        if cancel_event and cancel_event.is_set():
            return
        url = "https://www.camwhores.tv/models/" if p == 1 else f"https://www.camwhores.tv/models/{p}/"
        try:
            r = requests.get(url, headers=HEADERS, timeout=5)
            if r.status_code == 200:
                links = re.findall(r'href=["\']https://www\.camwhores\.tv/models/([^/"\'\s]+)/["\']', r.text)
                unique_models = list(dict.fromkeys(links))
                with ThreadPoolExecutor(max_workers=8) as executor:
                    futures = [executor.submit(_scan_model_page, model) for model in unique_models]
                    for future in as_completed(futures):
                        if cancel_event and cancel_event.is_set():
                            break
                        future.result()
                logger.info(f"[Scanner] Strona {p}/{max_pages} modelek przetworzona ({len(unique_models)} modelek).")
        except Exception as e:
            logger.warning(f"[Scanner] Błąd strony modelek {p}: {e}")

def scan_archivebate_directory(cancel_event=None):
    """4. Skanuje katalog profili Archivebate (litery a-z, cyfry)."""
    logger.info("[Scanner] Skanowanie katalogu Archivebate (A-Z)...")
    email, password = get_archivebate_credentials()
    s = ArchivebateSession(email=email, password=password)
    try:
        s.login()
    except Exception:
        pass

    letters = [chr(c) for c in range(ord('a'), ord('z') + 1)] + [str(d) for d in range(10)]
    all_users = set()

    for char in letters:
        if cancel_event and cancel_event.is_set():
            return
        try:
            headers = {
                "X-CSRF-TOKEN": s.csrf_token or "",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://archivebate.com/"
            }
            r = s.session.get(f"https://archivebate.com/api/v1/search?query={char}", headers=headers, timeout=4)
            if r.status_code == 200:
                data = r.json().get("data", [])
                for p in data:
                    u = p.get("username")
                    if u:
                        all_users.add(u)
        except Exception:
            pass

    logger.info(f"[Scanner] Znaleziono {len(all_users)} unikalnych profili na Archivebate. Rozpoczynam weryfikację płci i tagów...")
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(model_tag_manager.resolve_model, user) for user in all_users]
        for future in as_completed(futures):
            if cancel_event and cancel_event.is_set():
                break
            future.result()
    logger.info("[Scanner] Zakończono skanowanie profili z Archivebate.")

def wait_for_playback_idle(cancel_event=None):
    try:
        import main
        while main.is_playback_active() and not (cancel_event and cancel_event.is_set()):
            time.sleep(0.5)
        return not (cancel_event and cancel_event.is_set())
    except Exception:
        return not (cancel_event and cancel_event.is_set())

def run_full_quick_scan(cancel_event=None):
    """Uruchamia pełny, błyskawiczny skan wszystkich dostępnych źródeł (z ustępowaniem odtwarzaczowi)."""
    t0 = time.time()
    logger.info("=== START SZYBKIEGO SKANERA PROFILI ===")
    if cancel_event and cancel_event.is_set():
        return
    if not wait_for_playback_idle(cancel_event):
        return
    scan_user_store(cancel_event)
    if cancel_event and cancel_event.is_set():
        return
    if not wait_for_playback_idle(cancel_event):
        return
    scan_camwhores_tags(cancel_event)
    if cancel_event and cancel_event.is_set():
        return
    if not wait_for_playback_idle(cancel_event):
        return
    scan_camwhores_popular_models(max_pages=15, cancel_event=cancel_event)
    if cancel_event and cancel_event.is_set():
        return
    if not wait_for_playback_idle(cancel_event):
        return
    scan_archivebate_directory(cancel_event)
    t1 = time.time()
    logger.info(f"=== SKANOWANIE ZAKOŃCZONE w {t1 - t0:.1f} sekund! ===")


class QuickScanSupervisor:
    """One durable-in-process scan job with explicit lifecycle and cancellation."""

    def __init__(self):
        self._lock = threading.RLock()
        self._job = None
        self._thread = None
        self._cancel = threading.Event()

    def start(self) -> dict:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {**self.status(), "accepted": False, "status": "already_running"}
            job_id = uuid.uuid4().hex
            self._cancel = threading.Event()
            self._job = {
                "job_id": job_id,
                "status": "queued",
                "started_at": time.time(),
                "finished_at": None,
                "error": None,
            }
            self._thread = threading.Thread(target=self._run, args=(job_id, self._cancel), name="quick-profile-scan", daemon=True)
            self._thread.start()
            return {**self.status(), "accepted": True}

    def _run(self, job_id, cancel_event):
        with self._lock:
            if self._job and self._job["job_id"] == job_id:
                self._job["status"] = "running"
        try:
            run_full_quick_scan(cancel_event=cancel_event)
            final = "cancelled" if cancel_event.is_set() else "completed"
            error = None
        except Exception as exc:
            final, error = "failed", str(exc)
            logger.exception("Quick scan failed")
        with self._lock:
            if self._job and self._job["job_id"] == job_id:
                self._job["status"] = final
                self._job["error"] = error
                self._job["finished_at"] = time.time()

    def stop(self) -> dict:
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                return {**self.status(), "accepted": False}
            self._cancel.set()
            if self._job:
                self._job["status"] = "cancelling"
            return {**self.status(), "accepted": True}

    def status(self) -> dict:
        with self._lock:
            return dict(self._job or {"job_id": None, "status": "idle", "started_at": None, "finished_at": None, "error": None})

    def shutdown(self, timeout: float = 10.0) -> None:
        """Cancel the active scan and wait for its worker before application teardown."""
        self.stop()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
        if thread and thread.is_alive():
            raise RuntimeError("quick profile scan did not drain before shutdown")


quick_scan_supervisor = QuickScanSupervisor()

if __name__ == "__main__":
    run_full_quick_scan()
