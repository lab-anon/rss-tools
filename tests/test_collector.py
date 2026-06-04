from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import rss_tools.core.collector as collector
import rss_tools.core.config as config
import rss_tools.core.feed as feed
import rss_tools.core.http as http
import rss_tools.core.identity as identity
import rss_tools.core.models as models
import rss_tools.core.state as state

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
OLD = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)


def test_config_files_are_valid() -> None:
    repo_root = Path(__file__).parents[1]
    sources = config.load_feed_sources(repo_root / "feeds.json")
    mergers = config.load_mergers(repo_root / "mergers.json")
    retention = config.effective_source_retention_days(sources, mergers)
    source_slugs = {source.slug for source in sources}
    referenced_source_slugs = {
        source_slug for merger in mergers for source_slug in merger.feeds
    }

    assert sources
    assert mergers
    assert referenced_source_slugs == source_slugs
    assert set(retention) == source_slugs
    assert all(days > 0 for days in retention.values())


def test_effective_source_retention_uses_max_consumer_retention(tmp_path: Path) -> None:
    feeds_path, mergers_path = _write_configs(
        tmp_path,
        feeds=[
            {"slug": "shared", "link": "https://feed.test/rss/", "refresh_hours": 6}
        ],
        mergers=[
            _merger("short", ["shared"], retention_days=7),
            _merger("long", ["shared"], retention_days=30),
        ],
    )

    retention = config.effective_source_retention_days(
        config.load_feed_sources(feeds_path),
        config.load_mergers(mergers_path),
    )

    assert retention == {"shared": 30}


def test_collector_skips_source_until_refresh_window_elapses(tmp_path: Path) -> None:
    feeds_path, mergers_path = _write_default_configs(tmp_path)
    state_dir = tmp_path / ".state" / "sources"
    previous = _source_state(
        last_checked_at=NOW - timedelta(hours=1),
        last_success_at=NOW - timedelta(hours=1),
        last_changed_at=OLD,
        items=(_cached_item("Known", "https://example.test/known/", NOW),),
    )
    state.write_source_state(state_dir, previous)

    def fetcher(url: str) -> bytes:
        raise AssertionError(f"unexpected fetch: {url}")

    result = collector.collect_sources(
        feeds_path=feeds_path,
        mergers_path=mergers_path,
        state_dir=state_dir,
        fetcher=fetcher,
        now=NOW,
    )

    current = state.load_source_state(state_dir, "source")
    assert result.source_results[0].status == "skipped"
    assert current == previous


def test_collector_refreshes_when_source_config_changes(tmp_path: Path) -> None:
    feeds_path, mergers_path = _write_configs(
        tmp_path,
        feeds=[
            {
                "slug": "source",
                "link": "https://new-feed.test/rss/",
                "refresh_hours": 6,
            }
        ],
        mergers=[_merger("merged", ["source"], retention_days=7)],
    )
    state_dir = tmp_path / ".state" / "sources"
    state.write_source_state(
        state_dir,
        _source_state(
            last_checked_at=NOW - timedelta(hours=1),
            last_success_at=NOW - timedelta(hours=1),
            last_changed_at=OLD,
            items=(),
        ),
    )
    requests: list[str] = []

    def fetcher(url: str) -> bytes:
        requests.append(url)
        return _rss([("New", "https://example.test/new/", NOW)])

    result = collector.collect_sources(
        feeds_path=feeds_path,
        mergers_path=mergers_path,
        state_dir=state_dir,
        fetcher=fetcher,
        now=NOW,
        max_pages=1,
    )

    current = state.load_source_state(state_dir, "source")
    assert result.source_results[0].status == "refreshed"
    assert requests == ["https://new-feed.test/rss/"]
    assert current is not None
    assert current.link == "https://new-feed.test/rss/"
    assert current.items[0].title == "New"


def test_collector_delays_between_refreshed_sources(tmp_path: Path) -> None:
    feeds_path, mergers_path = _write_configs(
        tmp_path,
        feeds=[
            {"slug": "a", "link": "https://feed.test/a/", "refresh_hours": 6},
            {"slug": "b", "link": "https://feed.test/b/", "refresh_hours": 6},
        ],
        mergers=[_merger("merged", ["a", "b"], retention_days=7)],
    )
    requests: list[str] = []
    sleeps: list[float] = []
    starts: list[str] = []
    results: list[str] = []

    def fetcher(url: str) -> bytes:
        requests.append(url)
        return _rss([(url, f"https://example.test/{len(requests)}/", NOW)])

    collector.collect_sources(
        feeds_path=feeds_path,
        mergers_path=mergers_path,
        state_dir=tmp_path / ".state" / "sources",
        fetcher=fetcher,
        now=NOW,
        max_pages=1,
        source_delay_seconds=10,
        sleep_fn=sleeps.append,
        on_source_start=lambda source: starts.append(source.slug),
        on_source_result=lambda result: results.append(result.slug),
    )

    assert requests == ["https://feed.test/a/", "https://feed.test/b/"]
    assert sleeps == [10]
    assert starts == ["a", "b"]
    assert results == ["a", "b"]


def test_collector_does_not_overwrite_corrupt_previous_state(tmp_path: Path) -> None:
    feeds_path, mergers_path = _write_default_configs(tmp_path)
    state_path = tmp_path / ".state" / "sources" / "source.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        collector.collect_sources(
            feeds_path=feeds_path,
            mergers_path=mergers_path,
            state_dir=state_path.parent,
            fetcher=lambda url: _rss([]),
            now=NOW,
        )

    assert state_path.read_text(encoding="utf-8") == "{not json"


def test_collector_handles_304_without_marking_changed(tmp_path: Path) -> None:
    feeds_path, mergers_path = _write_default_configs(tmp_path)
    state_dir = tmp_path / ".state" / "sources"
    previous = _source_state(
        last_checked_at=OLD,
        last_success_at=OLD,
        last_changed_at=OLD,
        etag="etag-1",
        last_modified="Mon, 01 Jun 2026 08:00:00 +0000",
        items=(_cached_item("Known", "https://example.test/known/", NOW),),
    )
    state.write_source_state(state_dir, previous)
    seen_headers: list[dict[str, str] | None] = []

    def fetcher(
        url: str, *, headers: dict[str, str] | None = None
    ) -> models.HttpResponse:
        del url
        seen_headers.append(headers)
        return models.HttpResponse(content=b"", status_code=304)

    result = collector.collect_sources(
        feeds_path=feeds_path,
        mergers_path=mergers_path,
        state_dir=state_dir,
        fetcher=fetcher,
        now=NOW,
    )

    current = state.load_source_state(state_dir, "source")
    assert result.source_results[0].status == "not_modified"
    assert seen_headers == [
        {
            "If-None-Match": "etag-1",
            "If-Modified-Since": "Mon, 01 Jun 2026 08:00:00 +0000",
        }
    ]
    assert current is not None
    assert current.last_checked_at == NOW
    assert current.last_success_at == NOW
    assert current.last_changed_at == OLD
    assert current.etag == "etag-1"
    assert current.last_modified == "Mon, 01 Jun 2026 08:00:00 +0000"
    assert current.items == previous.items
    assert current.last_error is None


def test_collector_304_persists_changed_refresh_hours(tmp_path: Path) -> None:
    feeds_path, mergers_path = _write_configs(
        tmp_path,
        feeds=[
            {"slug": "source", "link": "https://feed.test/rss/", "refresh_hours": 12}
        ],
        mergers=[_merger("merged", ["source"], retention_days=7)],
    )
    state_dir = tmp_path / ".state" / "sources"
    previous = _source_state(
        last_checked_at=NOW - timedelta(hours=1),
        last_success_at=NOW - timedelta(hours=1),
        last_changed_at=OLD,
        refresh_hours=6,
        etag="etag-1",
    )
    state.write_source_state(state_dir, previous)

    def fetcher(
        url: str, *, headers: dict[str, str] | None = None
    ) -> models.HttpResponse:
        assert url == "https://feed.test/rss/"
        assert headers == {"If-None-Match": "etag-1"}
        return models.HttpResponse(content=b"", status_code=304)

    collector.collect_sources(
        feeds_path=feeds_path,
        mergers_path=mergers_path,
        state_dir=state_dir,
        fetcher=fetcher,
        now=NOW,
    )

    current = state.load_source_state(state_dir, "source")
    assert current is not None
    assert current.refresh_hours == 12
    assert current.link == previous.link
    assert current.last_changed_at == OLD


def test_collector_skips_conditional_headers_when_source_link_changes(
    tmp_path: Path,
) -> None:
    feeds_path, mergers_path = _write_configs(
        tmp_path,
        feeds=[
            {
                "slug": "source",
                "link": "https://new-feed.test/rss/",
                "refresh_hours": 6,
            }
        ],
        mergers=[_merger("merged", ["source"], retention_days=7)],
    )
    state_dir = tmp_path / ".state" / "sources"
    state.write_source_state(
        state_dir,
        _source_state(
            last_checked_at=NOW - timedelta(hours=1),
            last_success_at=NOW - timedelta(hours=1),
            last_changed_at=OLD,
            etag="etag-1",
            last_modified="Mon, 01 Jun 2026 08:00:00 +0000",
        ),
    )
    seen_headers: list[dict[str, str] | None] = []

    def fetcher(url: str, *, headers: dict[str, str] | None = None) -> bytes:
        assert url == "https://new-feed.test/rss/"
        seen_headers.append(headers)
        return _rss([("New", "https://example.test/new/", NOW)])

    collector.collect_sources(
        feeds_path=feeds_path,
        mergers_path=mergers_path,
        state_dir=state_dir,
        fetcher=fetcher,
        now=NOW,
        max_pages=1,
    )

    current = state.load_source_state(state_dir, "source")
    assert seen_headers == [None]
    assert current is not None
    assert current.link == "https://new-feed.test/rss/"


def test_collector_failure_preserves_existing_cache_fields(tmp_path: Path) -> None:
    feeds_path, mergers_path = _write_default_configs(tmp_path)
    state_dir = tmp_path / ".state" / "sources"
    previous = _source_state(
        last_checked_at=OLD,
        last_success_at=OLD,
        last_changed_at=OLD,
        etag="etag-1",
        last_modified="Mon, 01 Jun 2026 08:00:00 +0000",
        items=(_cached_item("Known", "https://example.test/known/", NOW),),
    )
    state.write_source_state(state_dir, previous)

    def fetcher(url: str) -> bytes:
        del url
        raise http.FetchError("boom")

    result = collector.collect_sources(
        feeds_path=feeds_path,
        mergers_path=mergers_path,
        state_dir=state_dir,
        fetcher=fetcher,
        now=NOW,
    )

    current = state.load_source_state(state_dir, "source")
    assert result.source_results[0].status == "failed"
    assert current is not None
    assert current.items == previous.items
    assert current.etag == previous.etag
    assert current.last_modified == previous.last_modified
    assert current.last_success_at == previous.last_success_at
    assert current.last_changed_at == previous.last_changed_at
    assert current.last_checked_at == NOW
    assert current.last_error == "boom"


def test_collector_failure_without_previous_state_creates_empty_error_state(
    tmp_path: Path,
) -> None:
    feeds_path, mergers_path = _write_default_configs(tmp_path)
    state_dir = tmp_path / ".state" / "sources"

    def fetcher(url: str) -> bytes:
        del url
        raise http.FetchError("boom")

    collector.collect_sources(
        feeds_path=feeds_path,
        mergers_path=mergers_path,
        state_dir=state_dir,
        fetcher=fetcher,
        now=NOW,
    )

    current = state.load_source_state(state_dir, "source")
    assert current is not None
    assert current.items == ()
    assert current.last_checked_at == NOW
    assert current.last_success_at is None
    assert current.last_error == "boom"


def test_collector_paginates_and_stops_on_fully_known_page(tmp_path: Path) -> None:
    feeds_path, mergers_path = _write_default_configs(tmp_path)
    state_dir = tmp_path / ".state" / "sources"
    known = _cached_item(
        "Known", "https://example.test/known/", NOW - timedelta(days=1)
    )
    state.write_source_state(
        state_dir,
        _source_state(
            last_checked_at=OLD,
            last_success_at=OLD,
            last_changed_at=OLD,
            items=(known,),
        ),
    )
    requests: list[str] = []
    page1 = _rss(
        [
            ("New", "https://example.test/new/", NOW),
        ]
    )
    page2 = _rss(
        [
            ("Known", "https://example.test/known/", NOW - timedelta(days=1)),
        ]
    )

    def fetcher(url: str) -> bytes:
        requests.append(url)
        if url == "https://feed.test/rss/":
            return page1
        if url == "https://feed.test/rss/?paged=2":
            return page2
        raise AssertionError(f"unexpected fetch: {url}")

    collector.collect_sources(
        feeds_path=feeds_path,
        mergers_path=mergers_path,
        state_dir=state_dir,
        fetcher=fetcher,
        now=NOW,
        max_pages=10,
    )

    current = state.load_source_state(state_dir, "source")
    assert requests == [
        "https://feed.test/rss/",
        "https://feed.test/rss/?paged=2",
    ]
    assert current is not None
    assert [item.title for item in current.items] == ["New", "Known"]


def test_collector_stops_when_page_has_no_new_retained_items(tmp_path: Path) -> None:
    feeds_path, mergers_path = _write_default_configs(tmp_path)
    state_dir = tmp_path / ".state" / "sources"
    requests: list[str] = []
    old_page = _rss(
        [
            ("Too Old", "https://example.test/too-old/", NOW - timedelta(days=10)),
        ]
    )

    def fetcher(url: str) -> bytes:
        requests.append(url)
        if url == "https://feed.test/rss/":
            return old_page
        raise AssertionError(f"unexpected fetch: {url}")

    collector.collect_sources(
        feeds_path=feeds_path,
        mergers_path=mergers_path,
        state_dir=state_dir,
        fetcher=fetcher,
        now=NOW,
        max_pages=10,
    )

    current = state.load_source_state(state_dir, "source")
    assert requests == ["https://feed.test/rss/"]
    assert current is not None
    assert current.items == ()


def _write_default_configs(tmp_path: Path) -> tuple[Path, Path]:
    return _write_configs(
        tmp_path,
        feeds=[
            {"slug": "source", "link": "https://feed.test/rss/", "refresh_hours": 6}
        ],
        mergers=[_merger("merged", ["source"], retention_days=7)],
    )


def _write_configs(
    tmp_path: Path,
    *,
    feeds: list[dict[str, object]],
    mergers: list[dict[str, object]],
) -> tuple[Path, Path]:
    feeds_path = tmp_path / "feeds.json"
    mergers_path = tmp_path / "mergers.json"
    feeds_path.write_text(json.dumps(feeds), encoding="utf-8")
    mergers_path.write_text(json.dumps(mergers), encoding="utf-8")
    return feeds_path, mergers_path


def _merger(
    slug: str,
    feeds: list[str],
    *,
    retention_days: int,
) -> dict[str, object]:
    return {
        "slug": slug,
        "title": slug.title(),
        "description": f"{slug} feed",
        "feeds": feeds,
        "retention_days": retention_days,
    }


def _source_state(
    *,
    last_checked_at: datetime | None,
    last_success_at: datetime | None,
    last_changed_at: datetime | None,
    link: str = "https://feed.test/rss/",
    refresh_hours: int = 6,
    etag: str | None = None,
    last_modified: str | None = None,
    items: tuple[models.CachedItem, ...] = (),
) -> models.CachedSourceState:
    return models.CachedSourceState(
        slug="source",
        link=link,
        refresh_hours=refresh_hours,
        last_checked_at=last_checked_at,
        last_success_at=last_success_at,
        last_changed_at=last_changed_at,
        last_error=None,
        etag=etag,
        last_modified=last_modified,
        items=items,
        namespaces={},
    )


def _cached_item(title: str, link: str, published_at: datetime) -> models.CachedItem:
    stable_id = identity.stable_id_for_link(link)
    return models.CachedItem(
        stable_id=stable_id,
        normalized_link=identity.canonicalize_link(link),
        title=title,
        link=link,
        published_at=published_at,
        item_xml=(
            "<item>"
            f"<title>{title}</title>"
            f"<link>{link}</link>"
            "<guid>source-guid</guid>"
            f"<pubDate>{feed.format_feed_date(published_at)}</pubDate>"
            f"<description>{title}</description>"
            "</item>"
        ),
    )


def _rss(items: list[tuple[str, str, datetime]]) -> bytes:
    item_xml = "".join(
        "<item>"
        f"<title>{title}</title>"
        f"<link>{link}</link>"
        f"<pubDate>{feed.format_feed_date(published_at)}</pubDate>"
        f"<description>{title}</description>"
        "</item>"
        for title, link, published_at in items
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        "<title>Source</title>"
        "<link>https://feed.test/</link>"
        "<description>Source</description>"
        f"{item_xml}"
        "</channel></rss>"
    ).encode()
