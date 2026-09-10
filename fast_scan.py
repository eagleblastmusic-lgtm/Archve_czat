import os
import re
import json
import time
import logging
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

def scan_user_store():
    """1. Skanuje wszystkie profile z bazy użytkownika (ulubione, historia, obserwowani)."""
    user_store_file = os.path.join(os.path.dirname(__file__), "data", "user_store.json")
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
            list(executor.map(model_tag_manager.resolve_model, models))
        logger.info(f"[Scanner] Zakończono skanowanie profili użytkownika.")
    except Exception as e:
        logger.warning(f"[Scanner] Błąd skanowania user_store: {e}")

def scan_camwhores_tags():
    """2. Skanuje główne kategorie Camwhores i przypisuje tagi do modelek (głębokie skanowanie dla Trans)."""
    logger.info("[Scanner] Skanowanie kategorii Camwhores.tv...")
    for tag_name, _ in TAG_CATEGORIES:
        max_p = 25 if tag_name == "trans" else 3
        count = 0
        for p in range(1, max_p + 1):
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

def scan_camwhores_popular_models(max_pages=20):
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
        url = "https://www.camwhores.tv/models/" if p == 1 else f"https://www.camwhores.tv/models/{p}/"
        try:
            r = requests.get(url, headers=HEADERS, timeout=5)
            if r.status_code == 200:
                links = re.findall(r'href=["\']https://www\.camwhores\.tv/models/([^/"\'\s]+)/["\']', r.text)
                unique_models = list(dict.fromkeys(links))
                with ThreadPoolExecutor(max_workers=8) as executor:
                    list(executor.map(_scan_model_page, unique_models))
                logger.info(f"[Scanner] Strona {p}/{max_pages} modelek przetworzona ({len(unique_models)} modelek).")
        except Exception as e:
            logger.warning(f"[Scanner] Błąd strony modelek {p}: {e}")

def scan_archivebate_directory():
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
        list(executor.map(model_tag_manager.resolve_model, all_users))
    logger.info("[Scanner] Zakończono skanowanie profili z Archivebate.")

def wait_for_playback_idle():
    try:
        import main
        while main.is_playback_active():
            time.sleep(0.5)
    except Exception:
        pass

def run_full_quick_scan():
    """Uruchamia pełny, błyskawiczny skan wszystkich dostępnych źródeł (z ustępowaniem odtwarzaczowi)."""
    t0 = time.time()
    logger.info("=== START SZYBKIEGO SKANERA PROFILI ===")
    wait_for_playback_idle()
    scan_user_store()
    wait_for_playback_idle()
    scan_camwhores_tags()
    wait_for_playback_idle()
    scan_camwhores_popular_models(max_pages=15)
    wait_for_playback_idle()
    scan_archivebate_directory()
    t1 = time.time()
    logger.info(f"=== SKANOWANIE ZAKOŃCZONE w {t1 - t0:.1f} sekund! ===")

if __name__ == "__main__":
    run_full_quick_scan()
