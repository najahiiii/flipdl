#!/usr/bin/env python3
"""FlipHTML5 downloader CLI."""

from __future__ import annotations

import argparse
import asyncio
import sys

from downloader import DownloaderOptions, FlipHTML5Downloader


def build_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Download FlipHTML5 pages from a book URL."
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="FlipHTML5 public URL (e.g. https://fliphtml5.com/<pub>/<book>/Title)",
    )
    parser.add_argument("--out", default="download", help="Output directory")
    parser.add_argument(
        "--workers", type=int, default=6, help="Number of download workers"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Overwrite existing files"
    )
    parser.add_argument(
        "--pdf", default="", help="Output PDF path (default: <out>/<title>.pdf)"
    )
    return parser


def _prompt_url() -> str:
    while True:
        value = input("FlipHTML5 URL: ").strip()
        if value:
            return value
        print("Please enter a URL.")


def _prompt_yes_no(prompt: str, *, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        value = input(f"{prompt} ({hint}): ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please answer with 'y' or 'n'.")


def _run_downloader(downloader: FlipHTML5Downloader) -> int:
    """Run downloader coroutine and handle Ctrl+C without traceback."""
    try:
        return asyncio.run(downloader.run())
    except KeyboardInterrupt:
        print("\nDownload cancelled.", file=sys.stderr)
        return 130
    except RuntimeError as exc:
        print(f"error: runtime failure: {exc}", file=sys.stderr)
        return 2


def _make_downloader(url: str, args: argparse.Namespace) -> FlipHTML5Downloader:
    options = DownloaderOptions(
        out=args.out,
        workers=args.workers,
        overwrite=args.overwrite,
        pdf=args.pdf,
    )
    return FlipHTML5Downloader(url=url, options=options)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.url:
        downloader = _make_downloader(args.url, args)
        return _run_downloader(downloader)

    if not sys.stdin.isatty():
        print(
            "error: URL is required when running non-interactively.",
            file=sys.stderr,
        )
        return 2

    last_code = 0
    while True:
        try:
            url = _prompt_url()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.", file=sys.stderr)
            return 130

        downloader = _make_downloader(url, args)
        last_code = _run_downloader(downloader)

        try:
            if not _prompt_yes_no("Download another E-Book?", default=True):
                return last_code
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.", file=sys.stderr)
            return 130
        print()


if __name__ == "__main__":
    raise SystemExit(main())
