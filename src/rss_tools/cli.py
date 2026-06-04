from __future__ import annotations

import argparse
from pathlib import Path

import rss_tools.core.collector as collector
import rss_tools.core.merger as merger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rss-tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect", help="Refresh source caches")
    _add_config_arguments(collect_parser)

    merge_parser = subparsers.add_parser("merge", help="Build merged RSS feeds")
    merge_parser.add_argument(
        "--mergers",
        type=Path,
        default=Path("mergers.json"),
        help="Path to mergers.json.",
    )
    merge_parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(".state/sources"),
        help="Directory containing source state JSON files.",
    )
    merge_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for generated feeds.",
    )

    build_parser = subparsers.add_parser(
        "build", help="Collect sources and merge feeds"
    )
    _add_config_arguments(build_parser)
    build_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for generated feeds.",
    )

    args = parser.parse_args(argv)
    if args.command == "collect":
        collector.collect_sources(
            feeds_path=args.feeds,
            mergers_path=args.mergers,
            state_dir=args.state_dir,
            source_delay_seconds=args.source_delay_seconds,
            on_source_start=_print_collect_start,
            on_source_result=_print_collect_source_result,
        )
        return 0

    if args.command == "merge":
        result = merger.merge_feeds(
            mergers_path=args.mergers,
            state_dir=args.state_dir,
            output_dir=args.output_dir,
        )
        _print_merge_result(result)
        return 0

    collector_result = collector.collect_sources(
        feeds_path=args.feeds,
        mergers_path=args.mergers,
        state_dir=args.state_dir,
        source_delay_seconds=args.source_delay_seconds,
        on_source_start=_print_collect_start,
        on_source_result=_print_collect_source_result,
    )
    merge_result = merger.merge_feeds(
        mergers_path=args.mergers,
        state_dir=args.state_dir,
        output_dir=args.output_dir,
        feeds_path=args.feeds,
        collector_result=collector_result,
        capture_merger_errors=True,
    )
    _print_merge_result(merge_result)
    return 0


def _add_config_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--feeds",
        type=Path,
        default=Path("feeds.json"),
        help="Path to feeds.json.",
    )
    parser.add_argument(
        "--mergers",
        type=Path,
        default=Path("mergers.json"),
        help="Path to mergers.json.",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(".state/sources"),
        help="Directory containing source state JSON files.",
    )
    parser.add_argument(
        "--source-delay-seconds",
        type=float,
        default=0.0,
        help="Seconds to wait between source refreshes.",
    )


def _print_collect_start(source) -> None:
    print(f"{source.slug}: collecting...", flush=True)


def _print_collect_source_result(source) -> None:
    print(f"{source.slug}: {source.status} - {source.message}", flush=True)


def _print_merge_result(result) -> None:
    for feed in result.feed_results:
        print(f"{feed.slug}: {feed.status} - {feed.message} -> {feed.output_path}")
