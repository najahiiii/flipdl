"""PDF helpers."""

from __future__ import annotations

import io
import os
import threading

import img2pdf
import pikepdf
from tqdm import tqdm

from utils.text import short_label


class PDFBuildCancelled(Exception):
    """Raised when PDF build is cancelled by user request."""


def build_pdf_from_images(
    image_paths: list[str],
    pdf_path: str,
    title: str | None,
    description: str | None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Build a single PDF from image paths with per-page progress updates."""
    if not image_paths:
        raise ValueError("No images to build PDF")

    pdf_dir = os.path.dirname(pdf_path)
    if pdf_dir:
        os.makedirs(pdf_dir, exist_ok=True)

    merged = pikepdf.Pdf.new()
    try:
        with tqdm(total=len(image_paths), desc="pdf", unit="page", leave=False) as pbar:
            for image_path in image_paths:
                if cancel_event is not None and cancel_event.is_set():
                    raise PDFBuildCancelled("PDF build cancelled")
                pbar.set_description_str(short_label(os.path.basename(image_path)))
                try:
                    page_pdf = img2pdf.convert(image_path)
                    with pikepdf.open(io.BytesIO(page_pdf)) as one_page:
                        merged.pages.extend(one_page.pages)
                except (img2pdf.ImageOpenError, OSError, pikepdf.PdfError) as exc:
                    name = os.path.basename(image_path)
                    raise ValueError(
                        f"failed to process image '{name}': {exc}"
                    ) from exc
                pbar.update(1)

        if cancel_event is not None and cancel_event.is_set():
            raise PDFBuildCancelled("PDF build cancelled")
        if title:
            merged.docinfo["/Title"] = title
        if description:
            merged.docinfo["/Subject"] = description
        merged.save(pdf_path)
    except pikepdf.PdfError as exc:
        raise ValueError(str(exc)) from exc
    finally:
        merged.close()
