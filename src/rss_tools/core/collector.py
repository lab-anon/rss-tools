from __future__ import annotations

import dataclasses
import time
import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import rss_tools.core.config as config
import rss_tools.core.feed as feed
import rss_tools.core.http as http
import rss_tools.core.models as models
import rss_tools.core.state as source_state

MAX_SOURCE_PAGES = 10


def collect_sources(
    *,
    feeds_path,
    mergers_path,
    state_dir,
    fetcher: models.FetchResponse = http.fetch_response,
    now: datetime | None = None,
    request_settings: models.RequestSettings | None = None,
    max_pages: int = MAX_SOURCE_PAGES,
    source_delay_seconds: float = 0.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    on_source_start: Callable[[models.FeedSourceConfig], None] | None = None,
    on_source_result: Callable[[models.SourceCollectResult], None] | None = None,
) -> models.CollectorResult:
    resolved_now = _resolve_now(now)
    sources = config.load_feed_sources(feeds_path)
    mergers = config.load_mergers(mergers_path)
    retention_by_source = config.effective_source_retention_days(sources, mergers)

    if source_delay_seconds < 0:
        raise ValueError("source_delay_seconds must be greater than or equal to 0")

    results: list[models.SourceCollectResult] = []
    for index, source in enumerate(sources):
        if on_source_start is not None:
            on_source_start(source)

        result = collect_source(
            source=source,
            state_dir=state_dir,
            effective_retention_days=retention_by_source[source.slug],
            fetcher=fetcher,
            now=resolved_now,
            request_settings=request_settings,
            max_pages=max_pages,
        )
        results.append(result)
        if on_source_result is not None:
            on_source_result(result)

        has_next_source = index < len(sources) - 1
        if has_next_source and source_delay_seconds > 0 and result.status != "skipped":
            sleep_fn(source_delay_seconds)

    return models.CollectorResult(source_results=results)


def collect_source(
    *,
    source: models.FeedSourceConfig,
    state_dir,
    effective_retention_days: int,
    fetcher: models.FetchResponse = http.fetch_response,
    now: datetime | None = None,
    request_settings: models.RequestSettings | None = None,
    max_pages: int = MAX_SOURCE_PAGES,
) -> models.SourceCollectResult:
    resolved_now = _resolve_now(now)
    previous = source_state.load_source_state(state_dir, source.slug)
    if not _is_refresh_due(previous, source, resolved_now):
        return models.SourceCollectResult(
            slug=source.slug,
            status="skipped",
            item_count=len(previous.items) if previous is not None else 0,
            message="refresh window has not elapsed",
            last_success_at=previous.last_success_at if previous is not None else None,
            last_error=previous.last_error if previous is not None else None,
        )

    try:
        updated, status = _refresh_source(
            source=source,
            previous=previous,
            effective_retention_days=effective_retention_days,
            fetcher=fetcher,
            now=resolved_now,
            request_settings=request_settings,
            max_pages=max_pages,
        )
    except Exception as exc:
        error = str(exc) or exc.__class__.__name__
        failed = _failed_state(
            source=source, previous=previous, now=resolved_now, error=error
        )
        source_state.write_source_state(state_dir, failed)
        return models.SourceCollectResult(
            slug=source.slug,
            status="failed",
            item_count=len(failed.items),
            message=error,
            last_success_at=failed.last_success_at,
            last_error=error,
        )

    source_state.write_source_state(state_dir, updated)
    return models.SourceCollectResult(
        slug=source.slug,
        status=status,
        item_count=len(updated.items),
        message=f"{status}; cached {len(updated.items)} item(s)",
        last_success_at=updated.last_success_at,
        last_error=updated.last_error,
    )


def feed_page_url(feed_url: str, page: int) -> str:
    if page == 1:
        return feed_url

    parsed = urllib.parse.urlparse(feed_url)
    query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query_params = [(key, value) for key, value in query_params if key != "paged"]
    query_params.append(("paged", str(page)))
    return urllib.parse.urlunparse(
        parsed._replace(query=urllib.parse.urlencode(query_params))
    )


def _refresh_source(
    *,
    source: models.FeedSourceConfig,
    previous: models.CachedSourceState | None,
    effective_retention_days: int,
    fetcher: models.FetchResponse,
    now: datetime,
    request_settings: models.RequestSettings | None,
    max_pages: int,
) -> tuple[models.CachedSourceState, str]:
    retention_cutoff = now - timedelta(days=effective_retention_days)
    cached_ids = {item.stable_id for item in previous.items} if previous else set()
    seen_ids = set(cached_ids)
    fetched_items: list[models.FeedItem] = []
    namespaces = dict(previous.namespaces) if previous is not None else {}
    first_response_etag: str | None = None
    first_response_last_modified: str | None = None

    for page in range(1, max_pages + 1):
        try:
            response = http.fetch_response_with_settings(
                fetcher,
                feed_page_url(source.link, page),
                request_settings,
                headers=(_conditional_headers(source, previous) if page == 1 else None),
            )
        except http.NotFoundError:
            if page == 1:
                raise
            break

        if response.status_code == 304:
            return (
                _not_modified_state(source=source, previous=previous, now=now),
                "not_modified",
            )

        if page == 1:
            first_response_etag = response.etag
            first_response_last_modified = response.last_modified

        parsed = feed.parse_feed(response.content)
        namespaces.update(parsed.namespaces)
        if not parsed.items:
            break

        page_has_new_retained_item = any(
            item.stable_id not in seen_ids and item.date >= retention_cutoff
            for item in parsed.items
        )
        page_all_known_in_cache = all(
            item.stable_id in cached_ids for item in parsed.items
        )

        fetched_items.extend(parsed.items)
        seen_ids.update(item.stable_id for item in parsed.items)

        if not page_has_new_retained_item or page_all_known_in_cache:
            break

    retained_items = _merge_and_prune_items(
        previous=previous,
        fetched_items=fetched_items,
        namespaces=namespaces,
        retention_cutoff=retention_cutoff,
    )
    changed = previous is None or _cached_items_changed(previous.items, retained_items)
    return (
        models.CachedSourceState(
            slug=source.slug,
            link=source.link,
            refresh_hours=source.refresh_hours,
            last_checked_at=now,
            last_success_at=now,
            last_changed_at=now if changed else previous.last_changed_at,
            last_error=None,
            etag=first_response_etag,
            last_modified=first_response_last_modified,
            items=tuple(retained_items),
            namespaces=namespaces,
        ),
        "refreshed",
    )


def _not_modified_state(
    *,
    source: models.FeedSourceConfig,
    previous: models.CachedSourceState | None,
    now: datetime,
) -> models.CachedSourceState:
    base = previous if previous is not None else source_state.empty_source_state(source)
    return dataclasses.replace(
        base,
        link=source.link,
        refresh_hours=source.refresh_hours,
        last_checked_at=now,
        last_success_at=now,
        last_error=None,
    )


def _failed_state(
    *,
    source: models.FeedSourceConfig,
    previous: models.CachedSourceState | None,
    now: datetime,
    error: str,
) -> models.CachedSourceState:
    if previous is None:
        return source_state.empty_source_state(source, now=now, last_error=error)
    return dataclasses.replace(previous, last_checked_at=now, last_error=error)


def _merge_and_prune_items(
    *,
    previous: models.CachedSourceState | None,
    fetched_items: list[models.FeedItem],
    namespaces: dict[str, str],
    retention_cutoff: datetime,
) -> list[models.CachedItem]:
    items_by_id: dict[str, models.CachedItem] = {}
    if previous is not None:
        for item in previous.items:
            _upsert_cached_item(items_by_id, item)

    for item in fetched_items:
        _upsert_cached_item(items_by_id, _cached_item_from_feed_item(item, namespaces))

    retained = [
        item for item in items_by_id.values() if item.published_at >= retention_cutoff
    ]
    return sorted(
        retained,
        key=lambda item: (item.published_at, item.title.casefold(), item.link),
        reverse=True,
    )


def _cached_item_from_feed_item(
    item: models.FeedItem,
    namespaces: dict[str, str],
) -> models.CachedItem:
    return models.CachedItem(
        stable_id=item.stable_id,
        normalized_link=item.normalized_link,
        title=item.title,
        link=item.link,
        published_at=item.date,
        item_xml=feed.serialize_item_xml(item, namespaces),
    )


def _upsert_cached_item(
    items_by_id: dict[str, models.CachedItem],
    candidate: models.CachedItem,
) -> None:
    current = items_by_id.get(candidate.stable_id)
    if current is None or candidate.published_at >= current.published_at:
        items_by_id[candidate.stable_id] = candidate


def _cached_items_changed(
    previous_items: tuple[models.CachedItem, ...],
    current_items: list[models.CachedItem],
) -> bool:
    previous_signature = [
        (item.stable_id, item.published_at, item.item_xml) for item in previous_items
    ]
    current_signature = [
        (item.stable_id, item.published_at, item.item_xml) for item in current_items
    ]
    return previous_signature != current_signature


def _conditional_headers(
    source: models.FeedSourceConfig,
    previous: models.CachedSourceState | None,
) -> dict[str, str] | None:
    if previous is None:
        return None
    if previous.link != source.link:
        return None

    headers = {}
    if previous.etag is not None:
        headers["If-None-Match"] = previous.etag
    if previous.last_modified is not None:
        headers["If-Modified-Since"] = previous.last_modified
    return headers or None


def _is_refresh_due(
    previous: models.CachedSourceState | None,
    source: models.FeedSourceConfig,
    now: datetime,
) -> bool:
    if previous is None or previous.last_success_at is None:
        return True
    if previous.link != source.link or previous.refresh_hours != source.refresh_hours:
        return True
    return previous.last_success_at + timedelta(hours=source.refresh_hours) <= now


def _resolve_now(now: datetime | None) -> datetime:
    resolved = datetime.now(UTC) if now is None else now
    if resolved.tzinfo is None:
        return resolved.replace(tzinfo=UTC)
    return resolved.astimezone(UTC)
