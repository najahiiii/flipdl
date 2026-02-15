"""FlipHTML5 downloader."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import shutil
import sys
import textwrap
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup
from tqdm import tqdm

from utils.decode import decode_pages
from utils.pdf import PDFBuildCancelled, build_pdf_from_images
from utils.text import clean_description, sanitize_filename, short_label
from utils.url import normalize_share_url

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_SIZE = "large"
REQUEST_TIMEOUT = 30
DOWNLOAD_MAX_ATTEMPTS = 16
DOWNLOAD_BACKOFF_BASE = 0.4
DOWNLOAD_BACKOFF_MAX = 5.0
DOWNLOAD_BACKOFF_JITTER = 0.2
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


@dataclass(slots=True)
class DownloaderOptions:
    """User-facing downloader configuration."""

    out: str = "download"
    workers: int = 6
    overwrite: bool = False
    pdf: str = ""


@dataclass(slots=True)
class PreparedBook:
    """Resolved metadata and page tasks before download/build."""

    title: str | None
    description: str | None
    pages_dir: str
    tasks: list[tuple[int, str | None, str | None]]


class FlipHTML5Downloader:
    """Download pages from a FlipHTML5 book and orchestrate output generation."""

    def __init__(self, url: str, options: DownloaderOptions | None = None) -> None:
        opts = options or DownloaderOptions()
        self.url = url
        self.out = opts.out
        self.size = DEFAULT_SIZE
        self.workers = opts.workers
        self.overwrite = opts.overwrite
        self.pdf = opts.pdf

    async def run(self) -> int:
        """Execute download flow. Returns process exit code."""
        base_url = self._normalize_base_url()
        if base_url is None:
            return 2

        timeout = self._build_timeout()
        connector = aiohttp.TCPConnector(limit=max(self.workers * 2, 20))
        async with aiohttp.ClientSession(
            headers={"User-Agent": DEFAULT_UA},
            timeout=timeout,
            connector=connector,
        ) as session:
            return await self._run_with_session(base_url, session)

    def output_pdf_path(self, title: str | None) -> str:
        """Resolve final PDF path from title and user options."""
        if self.pdf:
            return self.pdf
        return os.path.join(self.out, f"{sanitize_filename(title)}.pdf")

    def _normalize_base_url(self) -> str | None:
        try:
            return normalize_share_url(self.url)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return None

    def _build_timeout(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(
            total=None,
            connect=REQUEST_TIMEOUT,
            sock_connect=REQUEST_TIMEOUT,
            sock_read=REQUEST_TIMEOUT,
        )

    async def _run_with_session(
        self,
        base_url: str,
        session: aiohttp.ClientSession,
    ) -> int:
        prepared = await self._prepare_book_data(base_url, session)
        if prepared is None:
            return 2

        try:
            _ok, _skipped, failed = await self._download_pages(
                session, prepared.tasks, prepared.pages_dir
            )
            if failed > 0:
                print(
                    "error: some pages failed to download; PDF not created",
                    file=sys.stderr,
                )
                return 2
            return await self._create_pdf(prepared)
        finally:
            shutil.rmtree(prepared.pages_dir, ignore_errors=True)

    async def _prepare_book_data(
        self,
        base_url: str,
        session: aiohttp.ClientSession,
    ) -> PreparedBook | None:
        html = await self._load_book_html(base_url, session)
        if html is None:
            return None

        config = await self._load_book_config(base_url, html, session)
        if config is None:
            return None

        pages = await self._decode_book_pages(config, session)
        if pages is None:
            return None

        meta = self._extract_metadata(html)
        title = meta.get("title")
        description = meta.get("description")
        self._print_book_info(title, description, len(pages))
        pages_dir = os.path.join(self.out, "_pages")
        tasks = self._build_download_tasks(base_url, pages, self.size)
        return PreparedBook(
            title=title,
            description=description,
            pages_dir=pages_dir,
            tasks=tasks,
        )

    async def _load_book_html(
        self,
        base_url: str,
        session: aiohttp.ClientSession,
    ) -> str | None:
        try:
            return await self._fetch_html(base_url, session)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            print(f"error: failed to fetch html: {exc}", file=sys.stderr)
            return None

    async def _load_book_config(
        self,
        base_url: str,
        html: str,
        session: aiohttp.ClientSession,
    ) -> dict | None:
        config_url = (
            self._find_config_url(html, base_url) or f"{base_url}javascript/config.js"
        )
        try:
            return await self._fetch_config(config_url, session)
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            json.JSONDecodeError,
        ) as exc:
            print(f"error: failed to fetch/parse config: {exc}", file=sys.stderr)
            return None

    async def _decode_book_pages(
        self,
        config: dict,
        session: aiohttp.ClientSession,
    ) -> list | None:
        pages_raw = config.get("fliphtml5_pages")
        if not isinstance(pages_raw, list):
            print("Book source: encrypted")
            print("Decoding pages...")
        pages = await decode_pages(pages_raw, session)
        if not pages:
            print(
                "error: fliphtml5_pages not found or could not be decoded",
                file=sys.stderr,
            )
            return None
        return pages

    async def _create_pdf(self, prepared: PreparedBook) -> int:
        pdf_name = self.output_pdf_path(prepared.title)
        image_paths = [
            os.path.join(prepared.pages_dir, task[2])
            for task in prepared.tasks
            if task[2]
        ]
        try:
            print("Creating PDF...")
            loop = asyncio.get_running_loop()
            cancel_event = threading.Event()
            build_future = loop.run_in_executor(
                None,
                build_pdf_from_images,
                image_paths,
                pdf_name,
                prepared.title,
                prepared.description,
                cancel_event,
            )
            await asyncio.shield(build_future)
        except asyncio.CancelledError:
            cancel_event.set()
            with contextlib.suppress(Exception):
                await asyncio.shield(build_future)
            if os.path.exists(pdf_name):
                with contextlib.suppress(OSError):
                    os.remove(pdf_name)
            raise
        except PDFBuildCancelled:
            print("error: PDF build cancelled", file=sys.stderr)
            return 130
        except (OSError, ValueError) as exc:
            print(f"error: failed to build PDF: {exc}", file=sys.stderr)
            return 2

        print(f"PDF: {pdf_name}")
        return 0

    async def _fetch_html(self, url: str, session: aiohttp.ClientSession) -> str:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.text()

    def _extract_metadata(self, html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        meta: dict[str, str] = {}

        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            meta["title"] = title_tag.string.strip()

        for m in soup.find_all("meta"):
            name = m.get("name")
            prop = m.get("property")
            content = m.get("content")
            if not content:
                continue
            if name:
                meta[name.lower()] = content
            if prop:
                meta[prop.lower()] = content

        title = (
            meta.get("og:title")
            or meta.get("twitter:title")
            or meta.get("title")
            or meta.get("description")
        )
        description = (
            meta.get("og:description")
            or meta.get("description")
            or meta.get("twitter:description")
        )

        return {"title": title, "description": description, "raw": meta}

    def _find_config_url(self, html: str, base_url: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script", src=True):
            src = script.get("src", "")
            if "javascript/config.js" in src:
                return urljoin(base_url, src)
        return None

    async def _fetch_config(
        self, config_url: str, session: aiohttp.ClientSession
    ) -> dict:
        async with session.get(config_url) as resp:
            resp.raise_for_status()
            text = (await resp.text()).strip()
        if text.startswith("var htmlConfig = "):
            text = text[len("var htmlConfig = ") :]
        if text.endswith(";"):
            text = text[:-1]
        return json.loads(text)

    def _build_download_tasks(self, base_url: str, pages, size: str):
        tasks = []
        for idx, page in enumerate(pages):
            filename = None
            if isinstance(page, str):
                filename = page
            elif isinstance(page, dict):
                n = page.get("n")
                if isinstance(n, list) and n:
                    filename = n[0]
                elif isinstance(n, str):
                    filename = n
            if not filename:
                tasks.append((idx, None, None))
                continue
            url = self._build_page_url(base_url, filename, size)
            out_name = self._safe_output_name(idx, filename)
            tasks.append((idx, url, out_name))
        return tasks

    def _safe_output_name(self, idx: int, filename: str) -> str | None:
        leaf = filename.replace("\\", "/").rsplit("/", 1)[-1]
        leaf = leaf.split("?", 1)[0].split("#", 1)[0]
        safe_leaf = sanitize_filename(leaf)
        if safe_leaf in {"", ".", ".."}:
            return None
        return f"{idx+1:03d}_{safe_leaf}"

    def _build_page_url(self, base_url: str, filename: str, size: str) -> str:
        """Build a valid page URL from a filename or relative path."""
        if filename.startswith("http://") or filename.startswith("https://"):
            return filename
        path = filename
        if path.startswith("./"):
            path = path[2:]
        if path.startswith("/"):
            path = path[1:]
        if path.startswith("files/"):
            return urljoin(base_url, path)
        return urljoin(base_url, f"files/{size}/{path}")

    async def _download_one(
        self,
        session: aiohttp.ClientSession,
        url: str,
        out_path: str,
    ) -> str:
        if not self.overwrite and os.path.exists(out_path):
            return "skip"
        tmp_path = f"{out_path}.part"
        for attempt in range(1, DOWNLOAD_MAX_ATTEMPTS + 1):
            try:
                out_dir = os.path.dirname(out_path)
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)
                async with session.get(url) as resp:
                    if resp.status != 200:
                        if (
                            self._is_retryable_status(resp.status)
                            and attempt < DOWNLOAD_MAX_ATTEMPTS
                        ):
                            retry_after = resp.headers.get("Retry-After")
                            delay = self._compute_backoff_delay(attempt, retry_after)
                            await asyncio.sleep(delay)
                            continue
                        return f"fail:{resp.status}"

                    with open(tmp_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(1024 * 1024):
                            if chunk:
                                f.write(chunk)
                    os.replace(tmp_path, out_path)
                    return "ok"
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < DOWNLOAD_MAX_ATTEMPTS:
                    delay = self._compute_backoff_delay(attempt, None)
                    await asyncio.sleep(delay)
                    continue
                return f"fail:{exc.__class__.__name__}"
            except OSError as exc:
                return f"fail:{exc.__class__.__name__}"
            finally:
                if os.path.exists(tmp_path):
                    with contextlib.suppress(OSError):
                        os.remove(tmp_path)
        return "fail:max_retries"

    def _is_retryable_status(self, status: int) -> bool:
        return status in RETRYABLE_STATUS

    def _compute_backoff_delay(
        self,
        attempt: int,
        retry_after: str | None,
    ) -> float:
        delay = self._parse_retry_after(retry_after)
        if delay is not None:
            return min(delay, DOWNLOAD_BACKOFF_MAX)

        backoff = DOWNLOAD_BACKOFF_BASE * (2 ** (attempt - 1))
        jitter = random.uniform(0.0, DOWNLOAD_BACKOFF_JITTER)
        return min(backoff + jitter, DOWNLOAD_BACKOFF_MAX)

    def _parse_retry_after(self, raw: str | None) -> float | None:
        if not raw:
            return None
        value = raw.strip()
        if not value:
            return None
        try:
            seconds = float(value)
            return max(seconds, 0.0)
        except ValueError:
            pass
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return max((dt - now).total_seconds(), 0.0)
        except (TypeError, ValueError, OverflowError):
            return None

    def _resolve_output_path(self, pages_dir: str, out_name: str) -> str | None:
        candidate = os.path.normpath(os.path.join(pages_dir, out_name))
        base_real = os.path.realpath(pages_dir)
        target_real = os.path.realpath(candidate)
        try:
            if os.path.commonpath([base_real, target_real]) != base_real:
                return None
        except ValueError:
            return None
        return candidate

    async def _download_pages(
        self,
        session: aiohttp.ClientSession,
        tasks,
        pages_dir: str,
    ) -> tuple[int, int, int]:
        total = len(tasks)
        ok = 0
        skipped = 0
        failed = 0
        semaphore = asyncio.Semaphore(max(self.workers, 1))

        async def worker(task):
            idx, url, out_name = task
            if not url or not out_name:
                return idx, "fail:no_filename", out_name
            out_path = self._resolve_output_path(pages_dir, out_name)
            if out_path is None:
                return idx, "fail:unsafe_path", out_name
            async with semaphore:
                status = await self._download_one(session, url, out_path)
            return idx, status, out_name

        print("Downloading pages...")
        futures = [asyncio.create_task(worker(task)) for task in tasks]
        try:
            with tqdm(total=total, desc="download", unit="page", leave=False) as pbar:
                for fut in asyncio.as_completed(futures):
                    _, status, out_name = await fut
                    pbar.set_description_str(short_label(out_name))
                    if status == "ok":
                        ok += 1
                    elif status == "skip":
                        skipped += 1
                    else:
                        failed += 1
                    pbar.update(1)
        except asyncio.CancelledError:
            for fut in futures:
                fut.cancel()
            await asyncio.gather(*futures, return_exceptions=True)
            raise

        return ok, skipped, failed

    def _print_book_info(
        self, title: str | None, description: str | None, pages: int
    ) -> None:
        short_desc = clean_description(description)
        print("Book")
        print(f"  Title: {title or '-'}")
        if short_desc:
            print("  Description:")
            wrapped = textwrap.fill(short_desc, width=76)
            for line in wrapped.splitlines():
                print(f"    {line}")
        print(f"  Pages: {pages}")
