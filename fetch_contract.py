"""Typed source-fetch outcomes; an empty list is never used as an error sentinel."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FetchResult:
    status: str
    items: List[Dict[str, Any]] = field(default_factory=list)
    source: str = ""
    page: Optional[int] = None
    error: Optional[str] = None
    end_reason: Optional[str] = None
    retry_after: Optional[float] = None
    has_more: Optional[bool] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in {"success", "confirmed_empty", "confirmed_end"}

    @property
    def retryable(self) -> bool:
        return self.status in {"retryable_error", "timeout", "rate_limited"}

    @classmethod
    def success(cls, items, **kwargs):
        return cls("success", list(items or []), **kwargs)

    @classmethod
    def empty(cls, **kwargs):
        return cls("confirmed_empty", [], **kwargs)

    @classmethod
    def error_result(cls, error, *, retryable=True, status=None, **kwargs):
        return cls(status or ("retryable_error" if retryable else "fatal_error"), [], error=str(error), **kwargs)


def coerce_fetch_result(value: Any, *, source: str = "", page: Optional[int] = None) -> FetchResult:
    if isinstance(value, FetchResult):
        return value
    if isinstance(value, list):
        return FetchResult.success(value, source=source, page=page) if value else FetchResult.empty(source=source, page=page)
    return FetchResult.error_result("invalid source response", retryable=False, source=source, page=page)
