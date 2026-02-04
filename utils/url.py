"""URL utilities for FlipHTML5 share links."""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_share_url(url: str) -> str:
    """Convert a share URL into the FlipHTML5 reader base URL."""
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise ValueError(
            "URL path must contain at least two segments: /<publisher>/<book>/..."
        )
    publisher, book = parts[0], parts[1]
    return f"https://online.fliphtml5.com/{publisher}/{book}/"
