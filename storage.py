import os
import copy
import re
import json
import logging
import threading
from datetime import datetime
from typing import List, Dict, Any, Optional
from cache_store import atomic_write_json

logger = logging.getLogger("archivebate_storage")

def _locked_method(fn):
    """Serializuje mutację i przy błędzie przywraca ostatni stan in-memory.

    Durable write jest częścią kontraktu sukcesu. Jeżeli zapis na dysk zawiedzie,
    wyjątek wraca do warstwy API, a pamięć nie udaje stanu, którego nie da się
    odtworzyć po restarcie.
    """
    def wrapped(self, *args, **kwargs):
        with self._lock:
            snapshot = copy.deepcopy(self.data)
            try:
                return fn(self, *args, **kwargs)
            except Exception:
                self.data = snapshot
                raise
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
STORE_FILE = os.path.join(DATA_DIR, "user_store.json")

def _sort_items_newest(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sortuje elementy od najnowszych."""
    def sort_key(v):
        try:
            added_at = str(v.get("added_at") or v.get("watched_at") or "")
            raw_id = re.sub(r'\D', '', str(v.get("id", "")))
            id_val = int(raw_id) if raw_id else 0
            return (added_at, id_val)
        except Exception:
            return ("", 0)
    return sorted(items, key=sort_key, reverse=True)

class UserStorage:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self._lock = threading.RLock()
        self.data: Dict[str, Any] = {
            "favorites": [],
            "history": [],
            "following": [],
            "last_synced": None
        }
        self.load()

    def load(self):
        """Wczytuje zapisane dane użytkownika z pliku JSON."""
        with self._lock:
            if os.path.exists(STORE_FILE):
                try:
                    with open(STORE_FILE, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        self.data = loaded
                except Exception as e:
                    logger.error(f"Błąd odczytu magazynu danych: {e}")

            counts = self.data.get("blocked_model_video_counts", {})
            if counts:
                self.data["blocked_videos_total"] = sum(counts.values())

    def save(self) -> bool:
        """Atomowo zapisuje stan; błąd durable commit jest błędem operacji."""
        with self._lock:
            atomic_write_json(STORE_FILE, self.data)
        return True

    # ULUBIONE
    def get_favorites(self) -> List[Dict[str, Any]]:
        return _sort_items_newest(self.data.get("favorites", []))

    def is_favorite(self, video_id: str) -> bool:
        return any(v.get("id") == str(video_id) for v in self.data.get("favorites", []))

    @_locked_method
    def add_favorite(self, video: Dict[str, Any]) -> bool:
        v_id = str(video.get("id"))
        if not self.is_favorite(v_id):
            item = {k: v for k, v in video.items() if k not in {"grouped_videos", "playlist", "is_grouped", "group_count"}}
            item["id"] = v_id
            item["added_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.data.setdefault("favorites", []).insert(0, item)
            self.save()
            return True
        return False

    @_locked_method
    def remove_favorite(self, video_id: str) -> bool:
        v_id = str(video_id)
        favs = self.data.get("favorites", [])
        new_favs = [v for v in favs if str(v.get("id")) != v_id]
        if len(new_favs) != len(favs):
            self.data["favorites"] = new_favs
            self.save()
            return True
        return False

    @_locked_method
    def toggle_favorite(self, video: Dict[str, Any]) -> bool:
        v_id = str(video.get("id"))
        if self.is_favorite(v_id):
            self.remove_favorite(v_id)
            return False
        else:
            self.add_favorite(video)
            return True

    def get_favorite_authors(self) -> List[str]:
        """Zwraca unikalną listę nazw autorów (lowercase), których filmy znajdują się w ulubionych."""
        favs = self.data.get("favorites", [])
        authors = set()
        for v in favs:
            u = v.get("username")
            if u and str(u).lower().strip() not in ["model", ""]:
                authors.add(str(u).lower().strip())
        return sorted(list(authors))

    # HISTORIA
    def get_history(self) -> List[Dict[str, Any]]:
        return _sort_items_newest(self.data.get("history", []))

    @_locked_method
    def record_history(self, video: Dict[str, Any]):
        v_id = str(video.get("id"))
        history = self.data.setdefault("history", [])
        # Usuń istniejący wpis jeśli istnieje, aby przenieść go na sam początek
        history[:] = [h for h in history if str(h.get("id")) != v_id]

        item = {k: v for k, v in video.items() if k not in {"grouped_videos", "playlist", "is_grouped", "group_count"}}
        item["id"] = v_id
        item["watched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        history.insert(0, item)
        if len(history) > 1000:
            self.data["history"] = history[:1000]
        self.save()

    @_locked_method
    def clear_history(self):
        self.data["history"] = []
        self.save()

    # OBSERWOWANE
    def get_following(self) -> List[Dict[str, Any]]:
        return _sort_items_newest(self.data.get("following", []))

    # SYNCHRONIZACJA Z ARCHIVEBATE
    @_locked_method
    def merge_remote_data(self, watchlater: List[Dict[str, Any]], history: List[Dict[str, Any]], following: List[Dict[str, Any]]):
        """Scalanie danych z konta Archivebate z danymi lokalnymi (bez duplikatów)."""
        # Scal ulubione (watchlater)
        favs = self.data.setdefault("favorites", [])
        fav_map = {str(v.get("id")): v for v in favs if v.get("id")}
        for v in watchlater:
            v_id = str(v.get("id"))
            if v_id and v_id not in fav_map:
                item = dict(v)
                item["id"] = v_id
                item["added_at"] = item.get("date") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                fav_map[v_id] = item
        self.data["favorites"] = list(fav_map.values())

        # Scal historię
        hist = self.data.setdefault("history", [])
        hist_map = {str(v.get("id")): v for v in hist if v.get("id")}
        for v in history:
            v_id = str(v.get("id"))
            if v_id and v_id not in hist_map:
                item = dict(v)
                item["id"] = v_id
                item["watched_at"] = item.get("date") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                hist_map[v_id] = item
        self.data["history"] = list(hist_map.values())

        # Scal obserwowane
        foll = self.data.setdefault("following", [])
        foll_map = {str(v.get("id")): v for v in foll if v.get("id")}
        for v in following:
            v_id = str(v.get("id"))
            if v_id and v_id not in foll_map:
                foll_map[v_id] = v
        self.data["following"] = list(foll_map.values())

        self.data["last_synced"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save()

    def search_stored_videos(self, query: str) -> List[Dict[str, Any]]:
        """Wyszukuje filmy z bazy użytkownika na podstawie tagów, keywords, opisu i modelki."""
        q = query.lower().replace("#", "").strip()
        if not q:
            return []
        results = []
        seen = set()
        all_items = self.data.get("favorites", []) + self.data.get("history", []) + self.data.get("following", [])
        for item in all_items:
            vid_id = str(item.get("id"))
            if not vid_id or vid_id in seen:
                continue
            tags = [str(t).lower() for t in item.get("tags", [])]
            kws = [str(k).lower() for k in item.get("keywords", [])]
            desc = str(item.get("description", "")).lower()
            username = str(item.get("username", "")).lower()
            if q in tags or any(q in t for t in tags) or q in kws or any(q in k for k in kws) or q in desc or q in username:
                seen.add(vid_id)
                results.append(item)
        return results

    # BLOKOWANIE / CZARNA LISTA MODELI
    def is_model_blocked(self, username: str) -> bool:
        if not username:
            return False
        norm = re.sub(r'[^a-z0-9]', '', str(username).lower())
        blocked = [re.sub(r'[^a-z0-9]', '', str(b).lower()) for b in self.data.get("blocked_models", [])]
        return norm in blocked

    @_locked_method
    def block_model(self, username: str, video_count: Optional[int] = None) -> dict:
        if not username:
            return {"success": False, "removed_videos": 0}
        norm = re.sub(r'[^a-z0-9]', '', str(username).lower())
        if not norm or norm in ["model", "null", "none"]:
            return {"success": False, "removed_videos": 0}
        blocked = self.data.setdefault("blocked_models", [])
        counts = self.data.setdefault("blocked_model_video_counts", {})

        # Zlicz i usuń wszystkie filmy tej modelki z favorites, history, following
        lib_removed = 0
        for key in ["favorites", "history", "following"]:
            before = len(self.data.get(key, []))
            self.data[key] = [v for v in self.data.get(key, []) if re.sub(r'[^a-z0-9]', '', str(v.get("username", "")).lower()) != norm]
            lib_removed += before - len(self.data.get(key, []))

        # Rzeczywista liczba usuniętych filmów tego autora z katalogu
        existing_count = counts.get(norm, 0)
        author_vids = video_count if (video_count and video_count > 0) else max(lib_removed, existing_count, 1)
        author_vids = max(author_vids, existing_count)
        counts[norm] = author_vids

        if not any(re.sub(r'[^a-z0-9]', '', str(b).lower()) == norm for b in blocked):
            blocked.append(username)

        # Łączna suma filmów wszystkich zablokowanych autorów
        self.data["blocked_videos_total"] = sum(counts.values())

        self.save()

        # Usuń modelkę z bazy tagów model_tags.json
        try:
            from model_tags import model_tag_manager
            with model_tag_manager._lock:
                if norm in model_tag_manager._db:
                    del model_tag_manager._db[norm]
                    model_tag_manager._dirty = True
        except Exception:
            pass

        return {"success": True, "removed_videos": author_vids}

    def get_blocked_stats(self) -> dict:
        """Zwraca statystyki blokowania: liczbę autorów i łączną liczbę usuniętych filmów."""
        counts = self.data.get("blocked_model_video_counts", {})
        total_vids = sum(counts.values()) if counts else self.data.get("blocked_videos_total", 0)
        return {
            "blocked_authors_count": len(self.get_blocked_models()),
            "blocked_videos_total": total_vids
        }

    @_locked_method
    def unblock_model(self, username: str) -> bool:
        norm = re.sub(r'[^a-z0-9]', '', str(username).lower())
        blocked = self.data.get("blocked_models", [])
        self.data["blocked_models"] = [b for b in blocked if re.sub(r'[^a-z0-9]', '', str(b).lower()) != norm]
        counts = self.data.get("blocked_model_video_counts", {})
        if norm in counts:
            del counts[norm]
        self.data["blocked_videos_total"] = sum(counts.values())
        self.save()
        return True

    def get_blocked_models(self) -> List[str]:
        return sorted(list(set(self.data.get("blocked_models", []))))

storage = UserStorage()
