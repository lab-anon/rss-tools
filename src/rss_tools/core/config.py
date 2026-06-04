from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import rss_tools.core.models as models


def load_feed_sources(path: Path | str) -> list[models.FeedSourceConfig]:
    payload = _load_json_array(path)
    sources = [
        models.FeedSourceConfig(
            slug=_required_str(item, "slug"),
            link=_required_str(item, "link"),
            refresh_hours=_required_int(item, "refresh_hours"),
        )
        for item in payload
    ]
    _validate_unique_slugs(source.slug for source in sources)
    return sources


def load_mergers(path: Path | str) -> list[models.MergerConfig]:
    payload = _load_json_array(path)
    mergers = [
        models.MergerConfig(
            slug=_required_str(item, "slug"),
            title=_required_str(item, "title"),
            description=_required_str(item, "description"),
            feeds=tuple(_required_str_list(item, "feeds")),
            retention_days=_required_int(item, "retention_days"),
        )
        for item in payload
    ]
    _validate_unique_slugs(merger.slug for merger in mergers)
    return mergers


def validate_config_links(
    sources: list[models.FeedSourceConfig],
    mergers: list[models.MergerConfig],
) -> None:
    source_slugs = {source.slug for source in sources}
    missing = sorted(
        {
            source_slug
            for merger in mergers
            for source_slug in merger.feeds
            if source_slug not in source_slugs
        }
    )
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"Merger config references unknown feed slug(s): {joined}")


def effective_source_retention_days(
    sources: list[models.FeedSourceConfig],
    mergers: list[models.MergerConfig],
) -> dict[str, int]:
    validate_config_links(sources, mergers)
    retention_by_source = {source.slug: 0 for source in sources}
    for merger in mergers:
        for source_slug in merger.feeds:
            retention_by_source[source_slug] = max(
                retention_by_source[source_slug],
                merger.retention_days,
            )
    return retention_by_source


def _load_json_array(path: Path | str) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError(f"Expected JSON array in {path}")
    if not all(isinstance(item, dict) for item in payload):
        raise ValueError(f"Expected JSON object entries in {path}")
    return payload


def _required_str(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Expected non-empty string field: {key}")
    return value


def _required_int(item: dict[str, Any], key: str) -> int:
    value = item.get(key)
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"Expected non-negative integer field: {key}")
    return value


def _required_str_list(item: dict[str, Any], key: str) -> list[str]:
    value = item.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"Expected non-empty string array field: {key}")
    if not all(isinstance(entry, str) and entry for entry in value):
        raise ValueError(f"Expected non-empty strings in array field: {key}")
    return value


def _validate_unique_slugs(slugs) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for slug in slugs:
        if slug in seen:
            duplicates.add(slug)
        seen.add(slug)
    if duplicates:
        joined = ", ".join(sorted(duplicates))
        raise ValueError(f"Duplicate slug(s): {joined}")
