from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import datetime
from xml.etree import ElementTree as ET


@dataclasses.dataclass(frozen=True)
class FeedSourceConfig:
    slug: str
    link: str
    refresh_hours: int


@dataclasses.dataclass(frozen=True)
class MergerConfig:
    slug: str
    title: str
    description: str
    feeds: tuple[str, ...]
    retention_days: int


@dataclasses.dataclass(frozen=True)
class CachedItem:
    stable_id: str
    normalized_link: str
    title: str
    link: str
    published_at: datetime
    item_xml: str


@dataclasses.dataclass(frozen=True)
class CachedSourceState:
    slug: str
    link: str
    refresh_hours: int
    last_checked_at: datetime | None
    last_success_at: datetime | None
    last_changed_at: datetime | None
    last_error: str | None
    etag: str | None
    last_modified: str | None
    items: tuple[CachedItem, ...]
    namespaces: dict[str, str]


@dataclasses.dataclass(frozen=True)
class FeedItem:
    stable_id: str
    normalized_link: str
    date: datetime
    title: str
    link: str
    element: ET.Element


@dataclasses.dataclass(frozen=True)
class ParsedFeed:
    items: list[FeedItem]
    namespaces: dict[str, str]


@dataclasses.dataclass(frozen=True)
class RequestSettings:
    timeout_seconds: int
    retries: int
    user_agent: str
    retryable_status_codes: frozenset[int]


@dataclasses.dataclass(frozen=True)
class HttpResponse:
    content: bytes
    status_code: int = 200
    etag: str | None = None
    last_modified: str | None = None


FetchResponse = Callable[..., HttpResponse | bytes]


@dataclasses.dataclass(frozen=True)
class SourceCollectResult:
    slug: str
    status: str
    item_count: int
    message: str
    last_success_at: datetime | None = None
    last_error: str | None = None


@dataclasses.dataclass(frozen=True)
class CollectorResult:
    source_results: list[SourceCollectResult]


@dataclasses.dataclass(frozen=True)
class MergedFeedResult:
    slug: str
    title: str
    description: str
    output_path: str
    item_count: int
    message: str
    status: str = "ok"
    last_success_at: datetime | None = None
    last_error: str | None = None
    public_url: str | None = None


@dataclasses.dataclass(frozen=True)
class MergerResult:
    output_dir: str
    feed_results: list[MergedFeedResult]
