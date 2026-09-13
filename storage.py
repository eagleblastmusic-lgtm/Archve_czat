"""Durable local user state with fail-safe recovery and cross-process serialization."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

from cache_store import atomic_write_json
from video_identity import VideoKey, strict_video_key

logger = logging.getLogger("archivebate_storage")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
STORE_FILE = os.path.abspath(os.getenv("ARCHIVEBATE_USER_STORE") or os.path.join(DATA_DIR, "user_store.json"))
STORE_SCHEMA_VERSION = 2


class StoreRecoveryRequired(RuntimeError):
    """Raised when a corrupt/unsupported store must be repaired or restored first."""


class StoreLockError(RuntimeError):
    """Raised when another Archivebite process owns the user-store writer lock."""


class InvalidVideoIdentity(ValueError):
    """Raised when a mutation would persist an ambiguous video identity."""


_PROCESS_LOCK_GUARD = threading.RLock()
_PROCESS_LOCK_PATHS: set[str] = set()


def _defaults() -> Dict[str, Any]:
    return {
        "schema_version": STORE_SCHEMA_VERSION,
        "preferences_version": 0,
        "favorites": [],
        "history": [],
        "following": [],
        "last_synced": None,
        "blocked_models": [],
        "blocked_model_video_counts": {},
        "blocked_videos_total": 0,
        "remote_outbox": [],
    }


def _sort_items_newest(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def sort_key(value: Dict[str, Any]):
        try:
            added_at = str(value.get("added_at") or value.get("watched_at") or "")
            raw_id = re.sub(r"\D", "", str(value.get("id", "")))
            id_value = int(raw_id) if raw_id else 0
            return added_at, id_value
        except Exception:
            return "", 0

    return sorted(items, key=sort_key, reverse=True)


def _locked_method(fn):
    """Serialize mutations and roll memory back if durable commit fails."""

    def wrapped(self, *args, **kwargs):
        with self._lock:
            self._ensure_mutable()
            snapshot = copy.deepcopy(self.data)
            try:
                return fn(self, *args, **kwargs)
            except Exception:
                self.data = snapshot
                raise

    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


class UserStorage:
    def __init__(
        self,
        store_file: Optional[str] = None,
        data_dir: Optional[str] = None,
        lock_file: Optional[str] = None,
        acquire_lock: bool = True,
    ):
        self.store_file = os.path.abspath(os.fspath(store_file or STORE_FILE))
        self.data_dir = os.path.abspath(os.fspath(data_dir or os.path.dirname(self.store_file) or DATA_DIR))
        self.lock_file = os.path.abspath(os.fspath(lock_file or f"{self.store_file}.lock"))
        os.makedirs(os.path.dirname(self.store_file), exist_ok=True)
        self._lock = threading.RLock()
        self._lock_handle = None
        self._closed = False
        self.data: Dict[str, Any] = _defaults()
        self._health: Dict[str, Any] = {
            "status": "initializing",
            "schema_version": STORE_SCHEMA_VERSION,
            "last_error": None,
            "original_exists": os.path.exists(self.store_file),
        }
        if acquire_lock:
            self._acquire_writer_lock()
        self.load()

    def _acquire_writer_lock(self) -> None:
        normalized = os.path.normcase(os.path.abspath(self.lock_file))
        with _PROCESS_LOCK_GUARD:
            if normalized in _PROCESS_LOCK_PATHS:
                raise StoreLockError(f"user store is already open in this process: {self.store_file}")
            _PROCESS_LOCK_PATHS.add(normalized)
        handle = None
        try:
            handle = open(self.lock_file, "a+b")
            handle.seek(0)
            if os.path.getsize(self.lock_file) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_handle = handle
        except Exception as exc:
            with _PROCESS_LOCK_GUARD:
                _PROCESS_LOCK_PATHS.discard(normalized)
            try:
                if handle:
                    handle.close()
            except Exception:
                pass
            raise StoreLockError(f"could not acquire user-store lock: {self.lock_file}") from exc

    def _release_writer_lock(self) -> None:
        handle = self._lock_handle
        self._lock_handle = None
        if handle is None:
            return
        normalized = os.path.normcase(os.path.abspath(self.lock_file))
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            handle.close()
        finally:
            with _PROCESS_LOCK_GUARD:
                _PROCESS_LOCK_PATHS.discard(normalized)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._release_writer_lock()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _validate_loaded(self, loaded: Any) -> Dict[str, Any]:
        if not isinstance(loaded, dict):
            raise ValueError("user store root must be an object")
        schema = loaded.get("schema_version", 1)
        if not isinstance(schema, int) or schema < 1 or schema > STORE_SCHEMA_VERSION:
            raise ValueError(f"unsupported user store schema: {schema!r}")
        for field in ("favorites", "history", "following", "blocked_models", "remote_outbox"):
            if field in loaded and not isinstance(loaded[field], list):
                raise ValueError(f"user store field {field!r} must be a list")
        if "blocked_model_video_counts" in loaded and not isinstance(loaded["blocked_model_video_counts"], dict):
            raise ValueError("blocked_model_video_counts must be an object")
        if "preferences_version" in loaded and (
            not isinstance(loaded["preferences_version"], int) or loaded["preferences_version"] < 0
        ):
            raise ValueError("preferences_version must be a non-negative integer")
        normalized = _defaults()
        normalized.update(copy.deepcopy(loaded))
        normalized["schema_version"] = STORE_SCHEMA_VERSION
        for field in ("favorites", "history", "following"):
            if any(not isinstance(item, dict) for item in normalized.get(field, [])):
                raise ValueError(f"user store field {field!r} contains a non-object item")
        return normalized

    def load(self) -> Dict[str, Any]:
        """Load without overwriting a corrupt original or creating a repair illusion."""
        with self._lock:
            self._ensure_open()
            if not os.path.exists(self.store_file):
                self.data = _defaults()
                self._health.update({"status": "ready", "original_exists": False, "last_error": None})
                return self.data
            try:
                with open(self.store_file, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                self.data = self._validate_loaded(loaded)
                self._health.update({
                    "status": "ready",
                    "schema_version": self.data.get("schema_version", STORE_SCHEMA_VERSION),
                    "original_exists": True,
                    "last_error": None,
                })
                self._recalculate_blocked_total()
            except Exception as exc:
                self.data = _defaults()
                self._health.update({
                    "status": "recovery_required",
                    "original_exists": True,
                    "last_error": str(exc),
                })
                logger.error("Błąd odczytu magazynu danych; wymagane recovery: %s", exc)
            return self.data

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("user store is closed")

    def _ensure_mutable(self) -> None:
        self._ensure_open()
        if self._health.get("status") != "ready":
            raise StoreRecoveryRequired(
                "user store requires recovery before mutation" if self._health.get("status") == "recovery_required" else
                f"user store is not writable: {self._health.get('status')}"
            )

    def health(self) -> Dict[str, Any]:
        with self._lock:
            backup = f"{self.store_file}.last-good.bak"
            return {
                **self._health,
                "schema_version": self.data.get("schema_version", STORE_SCHEMA_VERSION),
                "preferences_version": self.data.get("preferences_version", 0),
                "has_last_good_copy": os.path.exists(backup),
                "store_path": self.store_file,
            }

    get_health = health

    def _write_last_good_copy(self) -> None:
        if not os.path.exists(self.store_file):
            return
        parent = os.path.dirname(self.store_file)
        fd, tmp_path = tempfile.mkstemp(prefix=".user_store.last-good.", suffix=".tmp", dir=parent)
        os.close(fd)
        try:
            shutil.copy2(self.store_file, tmp_path)
            with open(tmp_path, "ab") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, f"{self.store_file}.last-good.bak")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _write_recovery_source_copy(self) -> None:
        """Preserve a corrupt source before an explicit restore replaces it."""
        if not os.path.exists(self.store_file):
            return
        target = f"{self.store_file}.recovery-source.bak"
        parent = os.path.dirname(self.store_file)
        fd, tmp_path = tempfile.mkstemp(prefix=".user_store.recovery-source.", suffix=".tmp", dir=parent)
        os.close(fd)
        try:
            shutil.copy2(self.store_file, tmp_path)
            with open(tmp_path, "ab") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, target)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def save(self, preserve_last_good: bool = True) -> bool:
        """Atomically persist after preserving the last known-good copy."""
        with self._lock:
            self._ensure_mutable()
            if preserve_last_good:
                self._write_last_good_copy()
            payload = copy.deepcopy(self.data)
            payload["schema_version"] = STORE_SCHEMA_VERSION
            atomic_write_json(self.store_file, payload)
            self.data = payload
            self._health.update({"status": "ready", "last_error": None, "original_exists": True})
        return True

    def _recalculate_blocked_total(self) -> None:
        counts = self.data.get("blocked_model_video_counts", {}) or {}
        blocked = {self._norm_author(value) for value in self.data.get("blocked_models", [])}
        self.data["blocked_videos_total"] = sum(int(counts.get(key, 0) or 0) for key in blocked)

    @staticmethod
    def _norm_author(value: Any) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value or "").lower())

    def _blocked_model_norms(self) -> Set[str]:
        """Return the normalized block projection once for a read operation."""
        normalized_values = set()
        for value in self.data.get("blocked_models", []):
            normalized = self._norm_author(value)
            if normalized:
                normalized_values.add(normalized)
        return normalized_values

    def _without_blocked_models(self, values: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        blocked = self._blocked_model_norms()
        visible = []
        for value in values:
            normalized = self._norm_author(value.get("username"))
            if not normalized or normalized not in blocked:
                visible.append(value)
        return visible

    @staticmethod
    def _strip_group_fields(video: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: value for key, value in video.items()
            if key not in {"grouped_videos", "playlist", "is_grouped", "group_count", "_groupedVideos"}
        }

    @staticmethod
    def _stored_key(item: Dict[str, Any]) -> Optional[VideoKey]:
        return VideoKey.from_video(item, require_source=True)

    def _require_key(self, video: Dict[str, Any]) -> VideoKey:
        try:
            return strict_video_key(video)
        except ValueError as exc:
            raise InvalidVideoIdentity(str(exc)) from exc

    @staticmethod
    def _same_key(item: Dict[str, Any], key: VideoKey) -> bool:
        stored = UserStorage._stored_key(item)
        return stored == key

    def _coerce_lookup_key(self, video_id: Any, source: Optional[str] = None) -> Optional[VideoKey]:
        if isinstance(video_id, dict):
            return VideoKey.from_video(video_id, require_source=True)
        return VideoKey.from_value(video_id, source=source)

    @property
    def preferences_version(self) -> int:
        return int(self.data.get("preferences_version", 0) or 0)

    def _bump_preferences(self) -> None:
        self.data["preferences_version"] = self.preferences_version + 1

    # ULUBIONE
    def get_favorites(self, include_blocked: bool = True) -> List[Dict[str, Any]]:
        values = self.data.get("favorites", [])
        if not include_blocked:
            values = self._without_blocked_models(values)
        return _sort_items_newest(copy.deepcopy(values))

    def get_favorite_keys(self) -> List[Dict[str, str]]:
        result = []
        for item in self.data.get("favorites", []):
            key = self._stored_key(item)
            if key:
                result.append(key.as_dict())
        return result

    def is_favorite(self, video_id: Any, source: Optional[str] = None) -> bool:
        key = self._coerce_lookup_key(video_id, source=source)
        if key:
            return any(self._same_key(value, key) for value in self.data.get("favorites", []))
        raw = str(video_id or "").strip()
        return bool(raw) and any(
            not value.get("source") and str(value.get("id") or "").strip() == raw
            for value in self.data.get("favorites", [])
        )

    @_locked_method
    def add_favorite(self, video: Dict[str, Any]) -> bool:
        key = self._require_key(video)
        if self.is_favorite(video):
            return False
        item = self._strip_group_fields(dict(video))
        item["source"] = key.source
        item["id"] = f"cw_{key.provider_id}" if key.source == "camwhores" else key.provider_id
        item["added_at"] = datetime.now(timezone.utc).isoformat()
        self.data.setdefault("favorites", []).insert(0, item)
        self._bump_preferences()
        self.save()
        return True

    @_locked_method
    def remove_favorite(self, video_id: Any, source: Optional[str] = None) -> bool:
        key = self._coerce_lookup_key(video_id, source=source)
        favs = self.data.get("favorites", [])
        if key:
            new_favs = [value for value in favs if not self._same_key(value, key)]
        else:
            raw = str(video_id or "").strip()
            new_favs = [value for value in favs if value.get("source") or str(value.get("id") or "") != raw]
        if len(new_favs) == len(favs):
            return False
        self.data["favorites"] = new_favs
        self._bump_preferences()
        self.save()
        return True

    @_locked_method
    def toggle_favorite(self, video: Dict[str, Any]) -> bool:
        key = self._require_key(video)
        if self.is_favorite(video):
            self.data["favorites"] = [value for value in self.data.get("favorites", []) if not self._same_key(value, key)]
            result = False
        else:
            item = self._strip_group_fields(dict(video))
            item["source"] = key.source
            item["id"] = f"cw_{key.provider_id}" if key.source == "camwhores" else key.provider_id
            item["added_at"] = datetime.now(timezone.utc).isoformat()
            self.data.setdefault("favorites", []).insert(0, item)
            result = True
        self._bump_preferences()
        self.save()
        return result

    def get_favorite_authors(self) -> List[str]:
        blocked = self._blocked_model_norms()
        authors = {
            str(value.get("username")).lower().strip()
            for value in self.data.get("favorites", [])
            if value.get("username") and str(value.get("username")).lower().strip() not in {"model", ""}
            and self._norm_author(value.get("username")) not in blocked
        }
        return sorted(authors)

    # HISTORIA
    def get_history(self, include_blocked: bool = True) -> List[Dict[str, Any]]:
        values = self.data.get("history", [])
        if not include_blocked:
            values = self._without_blocked_models(values)
        return _sort_items_newest(copy.deepcopy(values))

    @_locked_method
    def record_history(self, video: Dict[str, Any]) -> None:
        key = self._require_key(video)
        history = self.data.setdefault("history", [])
        history[:] = [value for value in history if not self._same_key(value, key)]
        item = self._strip_group_fields(dict(video))
        item["source"] = key.source
        item["id"] = f"cw_{key.provider_id}" if key.source == "camwhores" else key.provider_id
        item["watched_at"] = datetime.now(timezone.utc).isoformat()
        history.insert(0, item)
        self.data["history"] = history[:1000]
        self.save()

    @_locked_method
    def clear_history(self) -> None:
        self.data["history"] = []
        self.save()

    # OBSERWOWANE
    def get_following(self, include_blocked: bool = True) -> List[Dict[str, Any]]:
        values = self.data.get("following", [])
        if not include_blocked:
            values = self._without_blocked_models(values)
        return _sort_items_newest(copy.deepcopy(values))

    # SYNCHRONIZACJA
    def _merge_video_collection(self, field: str, incoming: Iterable[Dict[str, Any]], timestamp_field: str) -> None:
        values = self.data.setdefault(field, [])
        by_key: Dict[str, Dict[str, Any]] = {}
        legacy: Dict[str, Dict[str, Any]] = {}
        for value in values:
            key = self._stored_key(value)
            if key:
                by_key[key.as_string()] = value
            elif value.get("id"):
                legacy[str(value.get("id"))] = value
        for raw in incoming or []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            if self.is_model_blocked(item.get("username")):
                continue
            key = VideoKey.from_video(item, require_source=True)
            if not key:
                continue
            item["source"] = key.source
            item["id"] = f"cw_{key.provider_id}" if key.source == "camwhores" else key.provider_id
            item[timestamp_field] = item.get("date") or datetime.now(timezone.utc).isoformat()
            by_key.setdefault(key.as_string(), item)
        self.data[field] = list(by_key.values()) + list(legacy.values())

    @_locked_method
    def merge_remote_data(
        self,
        watchlater: List[Dict[str, Any]],
        history: List[Dict[str, Any]],
        following: List[Dict[str, Any]],
    ) -> None:
        self._merge_video_collection("favorites", watchlater, "added_at")
        self._merge_video_collection("history", history, "watched_at")
        self._merge_video_collection("following", following, "added_at")
        self.data["last_synced"] = datetime.now(timezone.utc).isoformat()
        self.save()

    def search_stored_videos(self, query: str) -> List[Dict[str, Any]]:
        q = str(query or "").lower().replace("#", "").strip()
        if not q:
            return []
        results, seen = [], set()
        all_items = self.get_favorites(False) + self.get_history(False) + self.get_following(False)
        for item in all_items:
            key = self._stored_key(item)
            identity = key.as_string() if key else str(item.get("id") or "")
            if not identity or identity in seen:
                continue
            tags = [str(value).lower() for value in item.get("tags", [])]
            keywords = [str(value).lower() for value in item.get("keywords", [])]
            haystack = " ".join([str(item.get("description", "")).lower(), str(item.get("username", "")).lower()])
            if q in tags or any(q in value for value in tags) or q in keywords or any(q in value for value in keywords) or q in haystack:
                seen.add(identity)
                results.append(item)
        return results

    # BLOKOWANIE / CZARNA LISTA — wyłącznie projekcja, bez kasowania danych.
    def is_model_blocked(self, username: str) -> bool:
        norm = self._norm_author(username)
        if not norm:
            return False
        return norm in self._blocked_model_norms()

    @_locked_method
    def block_model(self, username: str, video_count: Optional[int] = None) -> dict:
        norm = self._norm_author(username)
        if not norm or norm in {"model", "null", "none"}:
            return {"success": False, "hidden_videos": 0, "removed_videos": 0, "destructive": False}
        blocked = self.data.setdefault("blocked_models", [])
        counts = self.data.setdefault("blocked_model_video_counts", {})
        existing_count = int(counts.get(norm, 0) or 0)
        library_count = sum(
            1 for field in ("favorites", "history", "following")
            for value in self.data.get(field, [])
            if self._norm_author(value.get("username")) == norm
        )
        estimate = max(existing_count, int(video_count or 0), library_count, 1)
        was_blocked = any(self._norm_author(value) == norm for value in blocked)
        changed = not was_blocked or estimate != existing_count
        counts[norm] = estimate
        if not was_blocked:
            blocked.append(str(username).strip())
        self._recalculate_blocked_total()
        if changed:
            self._bump_preferences()
            self.save()
        return {
            "success": True,
            "hidden_videos": estimate,
            "removed_videos": 0,
            "destructive": False,
            "projection": "blocked_models",
            "preferences_version": self.preferences_version,
        }

    def get_blocked_stats(self) -> dict:
        counts = self.data.get("blocked_model_video_counts", {}) or {}
        total = sum(int(counts.get(self._norm_author(value), 0) or 0) for value in self.get_blocked_models())
        return {
            "blocked_authors_count": len(self.get_blocked_models()),
            "blocked_videos_total": total,
            "hidden_videos_estimate": total,
            "accuracy": "estimate",
            "non_destructive": True,
            "preferences_version": self.preferences_version,
        }

    @_locked_method
    def unblock_model(self, username: str) -> bool:
        norm = self._norm_author(username)
        blocked = self.data.get("blocked_models", [])
        new_blocked = [value for value in blocked if self._norm_author(value) != norm]
        if len(new_blocked) == len(blocked):
            return False
        self.data["blocked_models"] = new_blocked
        self._recalculate_blocked_total()
        self._bump_preferences()
        self.save()
        return True

    def get_blocked_models(self) -> List[str]:
        return sorted(set(str(value) for value in self.data.get("blocked_models", [])))

    # Remote mutation intent/status (durable and explicit; no implicit retry).
    @_locked_method
    def set_remote_intent(self, video: Dict[str, Any], desired: bool, status: str = "pending") -> dict:
        key = self._require_key(video)
        outbox = self.data.setdefault("remote_outbox", [])
        entry = {
            "source": key.source,
            "provider_id": key.provider_id,
            "desired": bool(desired),
            "status": str(status),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        outbox[:] = [value for value in outbox if not (value.get("source") == key.source and value.get("provider_id") == key.provider_id)]
        outbox.append(entry)
        self.save()
        return copy.deepcopy(entry)

    def get_remote_intent(self, video: Dict[str, Any]) -> Optional[dict]:
        key = VideoKey.from_video(video, require_source=True)
        if not key:
            return None
        for entry in reversed(self.data.get("remote_outbox", [])):
            if entry.get("source") == key.source and entry.get("provider_id") == key.provider_id:
                return copy.deepcopy(entry)
        return None

    @_locked_method
    def set_remote_status(self, video: Dict[str, Any], status: str, error: Optional[str] = None) -> Optional[dict]:
        key = self._require_key(video)
        found = None
        for entry in reversed(self.data.setdefault("remote_outbox", [])):
            if entry.get("source") == key.source and entry.get("provider_id") == key.provider_id:
                found = entry
                break
        if found is None:
            return None
        found["status"] = str(status)
        found["error"] = str(error) if error else None
        found["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.save()
        return copy.deepcopy(found)

    # Backup / restore
    def export_snapshot(self) -> dict:
        with self._lock:
            safe = copy.deepcopy(self.data)
            for key in ("credentials", "cookies", "session"):
                safe.pop(key, None)
            return {
                "format": "archivebite-user-store",
                "schema_version": STORE_SCHEMA_VERSION,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "data": safe,
            }

    @staticmethod
    def validate_snapshot(snapshot: dict) -> Dict[str, Any]:
        if not isinstance(snapshot, dict) or snapshot.get("format") != "archivebite-user-store":
            raise ValueError("invalid Archivebite user-store export")
        data = snapshot.get("data")
        if not isinstance(data, dict):
            raise ValueError("export data must be an object")
        validator = UserStorage.__new__(UserStorage)
        return validator._validate_loaded(data)

    def restore_snapshot(self, snapshot: dict) -> dict:
        """Validate and durably restore an export, including a corrupt-store recovery path."""
        with self._lock:
            self._ensure_open()
            restored = self.validate_snapshot(snapshot)
            previous_data = copy.deepcopy(self.data)
            previous_health = copy.deepcopy(self._health)
            was_recovery = previous_health.get("status") == "recovery_required"
            try:
                if was_recovery:
                    # A corrupt file must remain recoverable, but must not be mislabeled as a
                    # last-known-good backup. The explicit restore is the repair authority.
                    self._write_recovery_source_copy()
                else:
                    self._write_last_good_copy()
                self.data = restored
                self._health.update({
                    "status": "ready",
                    "schema_version": STORE_SCHEMA_VERSION,
                    "last_error": None,
                    "original_exists": True,
                })
                # The old file was either already backed up above or is the corrupt recovery
                # source. Do not copy that pre-restore file into last-good a second time.
                self.save(preserve_last_good=False)
            except Exception:
                self.data = previous_data
                self._health = previous_health
                raise
            return {
                "success": True,
                "schema_version": self.data["schema_version"],
                "preferences_version": self.preferences_version,
            }


storage = UserStorage()
