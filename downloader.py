"""FlipHTML5 download and PDF builder logic."""

from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import img2pdf
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader, PdfWriter
from tqdm import tqdm

from utils.decode import decode_pages
from utils.text import clean_description, sanitize_filename, short_label
from utils.url import normalize_share_url

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_SIZE = "large"


class FlipHTML5Downloader:
    """Download pages from a FlipHTML5 book and build a PDF."""

    def __init__(
        self,
        url: str,
        out: str = "download",
        workers: int = 6,
        overwrite: bool = False,
        save_config: bool = False,
        pdf: str = "",
        keep_pages: bool = False,
    ) -> None:
        self.url = url
        self.out = out
        self.size = DEFAULT_SIZE
        self.workers = workers
        self.overwrite = overwrite
        self.save_config = save_config
        self.pdf = pdf
        self.keep_pages = keep_pages

    def run(self) -> int:
        """Execute download flow. Returns process exit code."""
        try:
            base_url = normalize_share_url(self.url)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

        session = self._new_session()
        try:
            html = self._fetch_html(base_url, session)
        except requests.RequestException as exc:
            print(f"error: failed to fetch html: {exc}", file=sys.stderr)
            return 2

        meta = self._extract_metadata(html)
        config_url = (
            self._find_config_url(html, base_url) or f"{base_url}javascript/config.js"
        )

        try:
            config = self._fetch_config(config_url, session)
        except (requests.RequestException, json.JSONDecodeError) as exc:
            print(f"error: failed to fetch/parse config: {exc}", file=sys.stderr)
            return 2

        pages_raw = config.get("fliphtml5_pages")
        if not isinstance(pages_raw, list):
            print("Book source: encrypted")
            print("Decoding pages...")
        pages = decode_pages(pages_raw, session)
        if not pages:
            print(
                "error: fliphtml5_pages not found or could not be decoded",
                file=sys.stderr,
            )
            return 2

        title = meta.get("title")
        description = meta.get("description")
        self._print_book_info(title, description, len(pages))

        if self.save_config:
            os.makedirs(self.out, exist_ok=True)
            with open(
                os.path.join(self.out, "config.json"), "w", encoding="utf-8"
            ) as f:
                json.dump(config, f, indent=2)

        pages_dir = os.path.join(self.out, "_pages")
        tasks = self._build_download_tasks(base_url, pages, self.size)

        _ok, _skipped, failed = self._download_pages(tasks, pages_dir)
        if failed > 0:
            print(
                "error: some pages failed to download; PDF not created", file=sys.stderr
            )
            return 2

        pdf_name = self.pdf or os.path.join(self.out, f"{sanitize_filename(title)}.pdf")
        image_paths = [os.path.join(pages_dir, t[2]) for t in tasks if t[2]]
        try:
            print("Creating PDF...")
            self._build_pdf(image_paths, pdf_name, title, description)
        except (OSError, ValueError) as exc:
            print(f"error: failed to build PDF: {exc}", file=sys.stderr)
            return 2

        if not self.keep_pages:
            shutil.rmtree(pages_dir, ignore_errors=True)

        print(f"PDF: {pdf_name}")
        return 0

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": DEFAULT_UA})
        return session

    def _fetch_html(self, url: str, session: requests.Session) -> str:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.text

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

    def _fetch_config(self, config_url: str, session: requests.Session) -> dict:
        resp = session.get(config_url, timeout=30)
        resp.raise_for_status()
        text = resp.text.strip()
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
            out_name = f"{idx+1:03d}_{filename}"
            tasks.append((idx, url, out_name))
        return tasks

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

    def _download_one(self, session: requests.Session, url: str, out_path: str) -> str:
        if not self.overwrite and os.path.exists(out_path):
            return "skip"
        try:
            resp = session.get(url, stream=True, timeout=30)
        except requests.RequestException as exc:
            return f"fail:{exc.__class__.__name__}"
        if resp.status_code != 200:
            return f"fail:{resp.status_code}"
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
        return "ok"

    def _download_pages(self, tasks, pages_dir: str) -> tuple[int, int, int]:
        total = len(tasks)
        ok = 0
        skipped = 0
        failed = 0

        def worker(task):
            idx, url, out_name = task
            if not url:
                return idx, "fail:no_filename", out_name
            out_path = os.path.join(pages_dir, out_name)
            session = self._new_session()
            status = self._download_one(session, url, out_path)
            return idx, status, out_name

        print("Downloading pages...")
        if self.workers <= 1:
            with tqdm(total=total, desc="download", unit="page", leave=False) as pbar:
                for t in tasks:
                    _, status, out_name = worker(t)
                    pbar.set_description_str(short_label(out_name))
                    if status == "ok":
                        ok += 1
                    elif status == "skip":
                        skipped += 1
                    else:
                        failed += 1
                    pbar.update(1)
        else:
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                future_map = {ex.submit(worker, t): t for t in tasks}
                with tqdm(
                    total=total, desc="download", unit="page", leave=False
                ) as pbar:
                    for fut in as_completed(future_map):
                        _, status, out_name = fut.result()
                        pbar.set_description_str(short_label(out_name))
                        if status == "ok":
                            ok += 1
                        elif status == "skip":
                            skipped += 1
                        else:
                            failed += 1
                        pbar.update(1)

        return ok, skipped, failed

    def _build_pdf(
        self, image_paths, pdf_path: str, title: str | None, description: str | None
    ) -> None:
        if not image_paths:
            raise ValueError("No images to build PDF")

        os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
        with open(pdf_path, "wb") as out_file:
            paths = list(tqdm(image_paths, desc="pdf", unit="page", leave=False))
            out_file.write(img2pdf.convert(*paths))

        if title or description:
            reader = PdfReader(pdf_path)
            writer = PdfWriter()
            for page in reader.pages:
                writer.add_page(page)
            metadata = {}
            if title:
                metadata["/Title"] = title
            if description:
                metadata["/Subject"] = description
            if metadata:
                writer.add_metadata(metadata)
            with open(pdf_path, "wb") as out_file:
                writer.write(out_file)

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
