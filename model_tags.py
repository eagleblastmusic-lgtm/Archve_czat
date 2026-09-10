import os
import re
import json
import time
import logging
import threading
import requests
from cache_store import atomic_write_json
from typing import Dict, List, Any, Optional
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("model_tags")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
MODEL_TAGS_FILE = os.path.join(DATA_DIR, "model_tags.json")
os.makedirs(DATA_DIR, exist_ok=True)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9'
}

# Wstępna baza znanych profili trans i innych kategorii dla natychmiastowej odpowiedzi (0 ms)
INITIAL_SEED = {
    "melodyxlove": {"gender": "Trans", "tags": ["Trans", "Chaturbate"]},
    "andreaytatyts": {"gender": "Trans", "tags": ["Trans", "Camwhores"]},
    "laurenblonde": {"gender": "Trans", "tags": ["Trans", "Camwhores"]},
    "itschloelaurent": {"gender": "Trans", "tags": ["Trans", "Camwhores"]},
    "fayefayebaby": {"gender": "Trans", "tags": ["Trans", "Camwhores"]},
    "daisy_taylor": {"gender": "Trans", "tags": ["Trans"]},
    "baileyjay": {"gender": "Trans", "tags": ["Trans"]},
    "aubreykate": {"gender": "Trans", "tags": ["Trans"]},
    "natalia_mars": {"gender": "Trans", "tags": ["Trans"]},
    "venuslux": {"gender": "Trans", "tags": ["Trans"]}
}

class ModelTagManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._db: Dict[str, Dict[str, Any]] = {}
        self._dirty = False
        self._last_save = time.time()
        self._load()
        
        def _flusher():
            while True:
                time.sleep(2)
                if self._dirty:
                    with self._lock:
                        if self._dirty and self._save():
                            self._dirty = False
        threading.Thread(target=_flusher, daemon=True).start()

    def _load(self):
        with self._lock:
            # Załaduj bazę początkową
            for k, v in INITIAL_SEED.items():
                self._db[k.lower()] = v.copy()

            if os.path.exists(MODEL_TAGS_FILE):
                try:
                    with open(MODEL_TAGS_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            for k, v in data.items():
                                self._db[k.lower()] = v
                except Exception as e:
                    logger.warning(f"Błąd odczytu model_tags.json: {e}")

    def _save(self) -> bool:
        try:
            atomic_write_json(MODEL_TAGS_FILE, self._db)
            return True
        except Exception as e:
            logger.warning(f"Błąd zapisu model_tags.json: {e}")
            return False

    def get_model(self, username: str) -> Optional[Dict[str, Any]]:
        norm = re.sub(r'[^a-z0-9]', '', str(username).lower())
        with self._lock:
            return self._db.get(norm)

    def set_model(self, username: str, gender: Optional[str] = None, tags: Optional[List[str]] = None):
        norm = re.sub(r'[^a-z0-9]', '', str(username).lower())
        if not norm:
            return
        with self._lock:
            entry = self._db.get(norm, {"username": username, "gender": None, "tags": []})
            if gender:
                entry["gender"] = gender
            if tags:
                existing_tags = set(entry.get("tags", []))
                for t in tags:
                    existing_tags.add(t)
                entry["tags"] = sorted(list(existing_tags))
            entry["timestamp"] = time.time()
            self._db[norm] = entry
            self._dirty = True
            now = time.time()
            if now - self._last_save > 3.0:
                self._last_save = now
                if self._save():
                    self._dirty = False

    def get_models_with_tag(self, tag: str) -> List[str]:
        tag_lower = tag.lower().strip()
        matches = []
        with self._lock:
            for norm, data in self._db.items():
                tags_norm = [t.lower() for t in data.get("tags", [])]
                gender_norm = (data.get("gender") or "").lower()
                if tag_lower in tags_norm or tag_lower == gender_norm:
                    matches.append(data.get("username") or norm)
        return matches

    def resolve_model(self, username: str) -> Dict[str, Any]:
        """Rozpoznaje płeć i tagi profilu modelki na podstawie zewnętrznych platform (Chaturbate, Camwhores itp.)."""
        norm = re.sub(r'[^a-z0-9]', '', str(username).lower())
        if not norm:
            return {}

        with self._lock:
            cached = self._db.get(norm)
            if cached and cached.get("gender") and (time.time() - cached.get("timestamp", 0) < 86400 * 7):
                return cached

        gender = None
        tags = set()

        # 1. Sprawdzamy Chaturbate
        try:
            r_cb = requests.get(f"https://chaturbate.com/{username}/", headers=HEADERS, timeout=3)
            if r_cb.status_code == 200:
                # Wyszukujemy broadcaster_gender w dossier pokoju
                m_g = re.search(r'(?:broadcaster_gender|gender)(?:\\u0022|"):\s*(?:\\u0022|")([^"\\\\]+)', r_cb.text)
                if m_g:
                    g_val = m_g.group(1).lower()
                    if g_val in ["trans", "shemale", "ts"]:
                        gender = "Trans"
                        tags.add("Trans")
                    elif g_val == "female":
                        gender = "Female"
                        tags.add("Female")
                    elif g_val == "male":
                        gender = "Male"
                        tags.add("Male")
                    elif g_val == "couple":
                        gender = "Couple"
                        tags.add("Couple")
                else:
                    # Sprawdzamy HTML <div class="data">Trans</div>
                    m_h = re.search(r'<div class="label">Gender:?</div>\s*<div class="data">([^<]+)</div>', r_cb.text, re.I)
                    if m_h:
                        gh = m_h.group(1).strip().capitalize()
                        gender = gh
                        tags.add(gh)
        except Exception:
            pass

        # 2. Sprawdzamy Camwhores (strona wyszukiwania lub pierwsze wideo modelki)
        try:
            r_cw = requests.get(f"https://www.camwhores.tv/search/{username}/", headers=HEADERS, timeout=4)
            if r_cw.status_code == 200:
                # Szukamy linków do wideo
                m_vids = re.findall(rf'href=["\'](https://www\.camwhores\.tv/videos/\d+/[^"\']+)["\']', r_cw.text)
                if m_vids:
                    first_watch = m_vids[0]
                    r_watch = requests.get(first_watch, headers=HEADERS, timeout=4)
                    if r_watch.status_code == 200:
                        # Tagi z Camwhores
                        cw_tags = re.findall(r'href=["\']https://www\.camwhores\.tv/tags/([^/"]+)/["\']', r_watch.text)
                        for ct in cw_tags:
                            ct_clean = ct.strip().lower()
                            if ct_clean in ["trans", "transgender", "tranny", "transsexual", "ts", "shemale", "she-male", "ladyboy"]:
                                gender = "Trans"
                                tags.add("Trans")
                            elif ct_clean in ["milf", "teen", "anal", "squirt", "ebony", "latina", "asian", "bbw", "couple", "feet", "dildo"]:
                                tags.add(ct_clean.capitalize())
        except Exception:
            pass

        if gender or tags:
            self.set_model(username, gender, list(tags))
            return self.get_model(username) or {"username": username, "gender": gender, "tags": list(tags)}

        # Domyślnie Female jeśli nie wykryto nic innego
        return {"username": username, "gender": "Female", "tags": ["Female"]}

    def resolve_models_async(self, usernames: List[str]):
        """Rozpoznaje profile modelek w puli wątków w tle bez blokowania odpowiedzi."""
        def _worker():
            try:
                with ThreadPoolExecutor(max_workers=6) as executor:
                    for u in set(usernames):
                        if u and not self.get_model(u):
                            try:
                                executor.submit(self.resolve_model, u)
                            except RuntimeError:
                                pass
            except RuntimeError:
                pass
        threading.Thread(target=_worker, daemon=True).start()

    def enrich_video(self, v: Dict[str, Any]) -> Dict[str, Any]:
        """Wzbogaca tagi wideo o informacje z bazy profili i zewnętrznych serwisów."""
        username = v.get("username", "")
        if not username:
            return v

        model_info = self.get_model(username)
        if not model_info:
            return v

        tags = set(v.get("tags", []))
        gender = model_info.get("gender")
        model_tags = model_info.get("tags", [])

        for mt in model_tags:
            tags.add(mt)

        if gender == "Trans":
            tags.add("Trans")
            if "Female" in tags:
                tags.remove("Female")
        elif gender == "Male":
            tags.add("Male")
            if "Female" in tags:
                tags.remove("Female")
        elif gender == "Couple":
            tags.add("Couple")

        v["tags"] = sorted(list(tags))
        if gender:
            v["gender"] = gender
        return v

model_tag_manager = ModelTagManager()
