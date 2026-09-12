"""Canonical, source-scoped identities shared by storage, catalog and API layers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import urldefrag


KNOWN_SOURCES = frozenset({"archivebate", "camwhores"})


def normalize_source(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_provider_id(source: str, value: Any) -> str:
    provider_id = str(value or "").strip()
    if source == "camwhores":
        provider_id = re.sub(r"^cw_", "", provider_id, flags=re.IGNORECASE)
    return provider_id


def normalize_video_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    url, _ = urldefrag(url)
    return url.rstrip("/")


@dataclass(frozen=True, order=True)
class VideoKey:
    """Stable identity. Empty/source-less IDs are deliberately not representable."""

    source: str
    provider_id: str

    def __post_init__(self) -> None:
        source = normalize_source(self.source)
        provider_id = normalize_provider_id(source, self.provider_id)
        if source not in KNOWN_SOURCES:
            raise ValueError(f"unsupported video source: {source or '<empty>'}")
        if not provider_id or provider_id.lower() in {"none", "null", "undefined"}:
            raise ValueError("video provider_id must be non-empty")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "provider_id", provider_id)

    @classmethod
    def from_video(cls, video: Mapping[str, Any], *, require_source: bool = True) -> Optional["VideoKey"]:
        if not isinstance(video, Mapping):
            return None
        raw_id = str(video.get("id") or "").strip()
        explicit_source = normalize_source(video.get("source"))
        platform = normalize_source(video.get("platform"))

        if explicit_source:
            source = explicit_source
        elif raw_id.lower().startswith("cw_") or "camwhores" in platform:
            source = "camwhores"
        elif not require_source:
            source = "archivebate"
        else:
            # A bare numeric/string ID is ambiguous across providers.
            return None

        provider_id = normalize_provider_id(source, raw_id)
        if not provider_id:
            provider_id = normalize_video_url(video.get("url"))
        if not provider_id:
            return None
        try:
            return cls(source, provider_id)
        except ValueError:
            return None

    @classmethod
    def from_value(cls, value: Any, source: Optional[str] = None) -> Optional["VideoKey"]:
        if isinstance(value, Mapping):
            return cls.from_video(value, require_source=source is None)
        raw = str(value or "").strip()
        if not raw:
            return None
        selected_source = normalize_source(source)
        if not selected_source and raw.lower().startswith("cw_"):
            selected_source = "camwhores"
        if not selected_source:
            return None
        try:
            return cls(selected_source, raw)
        except ValueError:
            return None

    def as_string(self) -> str:
        return f"{self.source}:id:{self.provider_id}"

    def as_dict(self) -> dict[str, str]:
        return {"source": self.source, "provider_id": self.provider_id}


def strict_video_key(video: Mapping[str, Any]) -> VideoKey:
    key = VideoKey.from_video(video, require_source=True)
    if key is None:
        raise ValueError("video requires an explicit source and a non-empty provider ID or URL")
    return key
