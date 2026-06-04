from __future__ import annotations

import shutil
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import rss_tools.core.config as config
import rss_tools.core.feed as feed
import rss_tools.core.models as models
import rss_tools.core.state as source_state
import rss_tools.core.status as status
import rss_tools.settings as settings


def merge_feeds(
    *,
    mergers_path,
    state_dir,
    output_dir=None,
    now: datetime | None = None,
    feeds_path=None,
    collector_result: models.CollectorResult | None = None,
    capture_merger_errors: bool = False,
) -> models.MergerResult:
    resolved_now = _resolve_now(now)
    target_dir = _prepare_output_dir(output_dir)
    mergers = config.load_mergers(mergers_path)
    source_configs = (
        config.load_feed_sources(feeds_path) if feeds_path is not None else None
    )
    results = []
    for merger in mergers:
        try:
            result = _merge_one(
                merger=merger,
                state_dir=state_dir,
                output_dir=target_dir,
                now=resolved_now,
            )
        except Exception as exc:
            if not capture_merger_errors:
                raise
            result = _failed_merger_result(
                merger=merger,
                error=str(exc) or exc.__class__.__name__,
            )
        results.append(result)

    status.write_status_outputs(
        output_dir=target_dir,
        generated_at=resolved_now,
        source_configs=source_configs,
        source_slugs=_source_slugs_for_status(
            source_configs=source_configs,
            mergers=mergers,
            collector_result=collector_result,
        ),
        source_results=(
            collector_result.source_results if collector_result is not None else None
        ),
        merger_results=results,
        state_dir=state_dir,
    )
    return models.MergerResult(output_dir=str(target_dir), feed_results=results)


def _source_slugs_for_status(
    *,
    source_configs,
    mergers: list[models.MergerConfig],
    collector_result: models.CollectorResult | None,
) -> list[str]:
    if source_configs is not None:
        return [source.slug for source in source_configs]
    if collector_result is not None:
        return [result.slug for result in collector_result.source_results]
    return sorted({source_slug for merger in mergers for source_slug in merger.feeds})


def _failed_merger_result(
    *, merger: models.MergerConfig, error: str
) -> models.MergedFeedResult:
    output_path = f"{merger.slug}/feed.xml"
    public_url = urllib.parse.urljoin(
        f"{settings.PAGES_BASE_URL.rstrip('/')}/", output_path
    )
    return models.MergedFeedResult(
        slug=merger.slug,
        title=merger.title,
        description=merger.description,
        output_path=output_path,
        item_count=0,
        message=error,
        status="error",
        last_success_at=None,
        last_error=error,
        public_url=public_url,
    )


def _merge_one(
    *,
    merger: models.MergerConfig,
    state_dir,
    output_dir: Path,
    now: datetime,
) -> models.MergedFeedResult:
    retention_cutoff = now - timedelta(days=merger.retention_days)
    states = [
        state
        for source_slug in merger.feeds
        if (state := source_state.load_source_state(state_dir, source_slug)) is not None
    ]
    namespaces = _merged_namespaces(states)
    cached_items = _dedupe_cached_items(
        states_by_slug={state.slug: state for state in states},
        merger=merger,
        retention_cutoff=retention_cutoff,
    )
    feed_items = [
        _feed_item_from_cached_item(item, namespaces) for item in cached_items
    ]
    output_path = f"{merger.slug}/feed.xml"
    public_url = urllib.parse.urljoin(
        f"{settings.PAGES_BASE_URL.rstrip('/')}/", output_path
    )
    content = feed.render_feed(
        title=merger.title,
        description=merger.description,
        self_url=public_url,
        site_url=f"{settings.PAGES_BASE_URL.rstrip('/')}/",
        build_date=now,
        items=feed_items,
        namespaces=namespaces,
    )
    target_path = output_dir / output_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(content)
    return models.MergedFeedResult(
        slug=merger.slug,
        title=merger.title,
        description=merger.description,
        output_path=output_path,
        item_count=len(feed_items),
        message=f"merged {len(feed_items)} item(s)",
        status="ok",
        last_success_at=now,
        last_error=None,
        public_url=public_url,
    )


def _dedupe_cached_items(
    *,
    states_by_slug: dict[str, models.CachedSourceState],
    merger: models.MergerConfig,
    retention_cutoff: datetime,
) -> list[models.CachedItem]:
    items_by_id: dict[str, models.CachedItem] = {}
    for source_slug in merger.feeds:
        state = states_by_slug.get(source_slug)
        if state is None:
            continue
        for item in state.items:
            if item.published_at < retention_cutoff:
                continue
            current = items_by_id.get(item.stable_id)
            if current is None or item.published_at > current.published_at:
                items_by_id[item.stable_id] = item

    return sorted(
        items_by_id.values(),
        key=lambda item: (item.published_at, item.title.casefold(), item.link),
        reverse=True,
    )


def _feed_item_from_cached_item(
    item: models.CachedItem,
    namespaces: dict[str, str],
) -> models.FeedItem:
    return models.FeedItem(
        stable_id=item.stable_id,
        normalized_link=item.normalized_link,
        date=item.published_at,
        title=item.title,
        link=item.link,
        element=feed.parse_item_xml(item.item_xml, namespaces),
    )


def _merged_namespaces(states: list[models.CachedSourceState]) -> dict[str, str]:
    namespaces: dict[str, str] = {}
    for state in states:
        namespaces.update(state.namespaces)
    return namespaces


def _prepare_output_dir(output_dir) -> Path:
    target_dir = settings.BUILD_OUTPUT_DIR if output_dir is None else Path(output_dir)
    if target_dir.resolve() == Path.cwd().resolve():
        raise ValueError("Refusing to build into the repository root.")
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def _resolve_now(now: datetime | None) -> datetime:
    resolved = datetime.now(UTC) if now is None else now
    if resolved.tzinfo is None:
        return resolved.replace(tzinfo=UTC)
    return resolved.astimezone(UTC)
