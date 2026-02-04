#!/usr/bin/env python3
"""FlipHTML5 downloader CLI."""

from __future__ import annotations

import argparse

from downloader import FlipHTML5Downloader


def build_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Download FlipHTML5 pages from a book URL."
    )
    parser.add_argument(
        "url",
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
        "--save-config", action="store_true", help="Save config JSON next to downloads"
    )
    parser.add_argument(
        "--pdf", default="", help="Output PDF path (default: <out>/<title>.pdf)"
    )
    parser.add_argument(
        "--keep-pages", action="store_true", help="Keep downloaded page images"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    downloader = FlipHTML5Downloader(
        url=args.url,
        out=args.out,
        workers=args.workers,
        overwrite=args.overwrite,
        save_config=args.save_config,
        pdf=args.pdf,
        keep_pages=args.keep_pages,
    )
    return downloader.run()


if __name__ == "__main__":
    raise SystemExit(main())
