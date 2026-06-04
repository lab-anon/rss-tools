from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import rss_tools.core.models as models


def source_state_path(state_dir: Path | str, slug: str) -> Path:
    return Path(state_dir) / f"{slug}.json"


def load_source_state(
    state_dir: Path | str, slug: str
) -> models.CachedSourceState | None:
    path = source_state_path(state_dir, slug)
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return cached_source_state_from_json(payload)


def write_source_state(state_dir: Path | str, state: models.CachedSourceState) -> None:
    path = source_state_path(state_dir, state.slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            cached_source_state_to_json(state),
            handle,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")


def empty_source_state(
    source: models.FeedSourceConfig,
    *,
    now: datetime | None = None,
    last_error: str | None = None,
) -> models.CachedSourceState:
    return models.CachedSourceState(
        slug=source.slug,
        link=source.link,
        refresh_hours=source.refresh_hours,
        last_checked_at=now,
        last_success_at=None,
        last_changed_at=None,
        last_error=last_error,
        etag=None,
        last_modified=None,
        items=(),
        namespaces={},
    )


def cached_source_state_to_json(state: models.CachedSourceState) -> dict[str, Any]:
    return {
        "slug": state.slug,
        "link": state.link,
        "refresh_hours": state.refresh_hours,
        "last_checked_at": format_json_datetime(state.last_checked_at),
        "last_success_at": format_json_datetime(state.last_success_at),
        "last_changed_at": format_json_datetime(state.last_changed_at),
        "last_error": state.last_error,
        "etag": state.etag,
        "last_modified": state.last_modified,
        "items": [cached_item_to_json(item) for item in state.items],
        "namespaces": dict(sorted(state.namespaces.items())),
    }


def cached_source_state_from_json(payload: dict[str, Any]) -> models.CachedSourceState:
    return models.CachedSourceState(
        slug=_required_str(payload, "slug"),
        link=_required_str(payload, "link"),
        refresh_hours=_required_int(payload, "refresh_hours"),
        last_checked_at=parse_json_datetime(payload.get("last_checked_at")),
        last_success_at=parse_json_datetime(payload.get("last_success_at")),
        last_changed_at=parse_json_datetime(payload.get("last_changed_at")),
        last_error=_optional_str(payload.get("last_error")),
        etag=_optional_str(payload.get("etag")),
        last_modified=_optional_str(payload.get("last_modified")),
        items=tuple(cached_item_from_json(item) for item in payload.get("items", [])),
        namespaces=_string_dict(payload.get("namespaces", {})),
    )


def cached_item_to_json(item: models.CachedItem) -> dict[str, Any]:
    return {
        "stable_id": item.stable_id,
        "normalized_link": item.normalized_link,
        "title": item.title,
        "link": item.link,
        "published_at": format_json_datetime(item.published_at),
        "item_xml": item.item_xml,
    }


def cached_item_from_json(payload: dict[str, Any]) -> models.CachedItem:
    return models.CachedItem(
        stable_id=_required_str(payload, "stable_id"),
        normalized_link=_required_str(payload, "normalized_link"),
        title=_required_str(payload, "title"),
        link=_required_str(payload, "link"),
        published_at=_required_datetime(payload, "published_at"),
        item_xml=_required_str(payload, "item_xml"),
    )


def format_json_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    resolved = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return resolved.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_json_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("Expected ISO datetime string or null")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _required_datetime(payload: dict[str, Any], key: str) -> datetime:
    value = parse_json_datetime(payload.get(key))
    if value is None:
        raise ValueError(f"Expected datetime field: {key}")
    return value


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Expected non-empty string field: {key}")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Expected string or null")
    return value


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"Expected non-negative integer field: {key}")
    return value


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("Expected object")
    result: dict[str, str] = {}
    for key, entry in value.items():
        if not isinstance(key, str) or not isinstance(entry, str):
            raise ValueError("Expected string dictionary")
        result[key] = entry
    return result
