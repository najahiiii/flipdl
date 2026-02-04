"""Decode helpers for FlipHTML5 encrypted page lists."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import requests

DESTRING_URL = (
    "https://static.fliphtml5.com/resourceFiles/html5_templates/js/deString.js"
)


def decode_pages(pages_raw, session: requests.Session) -> list | None:
    """Return page list from raw config value, decoding if needed."""
    if isinstance(pages_raw, list):
        return pages_raw
    if isinstance(pages_raw, str):
        text = pages_raw.strip()
        if text.startswith("[") or text.startswith("{"):
            return parse_pages_json(text)
        decoded = destring(text, session)
        if not decoded:
            return None
        return parse_pages_json(decoded)
    return None


def parse_pages_json(text: str) -> list | None:
    """Parse a JSON array, tolerating extra prefix/suffix text."""
    raw = text.strip()
    if not raw.startswith("["):
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return None
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def ensure_destring_js(session: requests.Session) -> str:
    """Download and cache deString.js for decoding."""
    cache_dir = os.path.join(os.getcwd(), ".cache")
    os.makedirs(cache_dir, exist_ok=True)
    js_path = os.path.join(cache_dir, "deString.js")
    if os.path.exists(js_path) and os.path.getsize(js_path) > 0:
        return js_path
    resp = session.get(DESTRING_URL, timeout=30)
    resp.raise_for_status()
    with open(js_path, "wb") as f:
        f.write(resp.content)
    return js_path


def destring(value: str, session: requests.Session) -> str | None:
    """Decode encrypted text using the FlipHTML5 deString.js bundle."""
    if shutil.which("node") is None:
        print("error: node is required to decode fliphtml5_pages", file=sys.stderr)
        return None
    try:
        js_path = ensure_destring_js(session)
    except (requests.RequestException, OSError) as exc:
        print(f"error: failed to download deString.js: {exc}", file=sys.stderr)
        return None

    runner_path = os.path.join(os.path.dirname(__file__), "destring_runner.js")
    result = subprocess.run(
        ["node", runner_path],
        input=value,
        text=True,
        capture_output=True,
        env={**os.environ, "DESTRING_PATH": js_path},
        check=False,
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or "unknown error"
        print(f"error: destring failed: {msg}", file=sys.stderr)
        return None
    return result.stdout
