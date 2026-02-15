# flipdl

FlipHTML5 downloader that fetches pages and builds single PDF.

## What it does

- Accepts a public FlipHTML5 URL.
- Downloads all pages.
- Merges them into one PDF.

## Requirement

- Python 3.10+

## Install

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Usage

Interactive mode:

```bash
python main.py
```

Enter the `FlipHTML5 URL:` prompt and the process runs automatically until PDF is created.

One-shot mode:

```bash
python main.py https://fliphtml5.com/<pub>/<book>/Title
```

## CLI options

- `url` FlipHTML5 URL (optional in interactive mode).
- `--out` output folder (default: `download`).
- `--workers` number of download workers (default: `6`).
- `--overwrite` overwrite existing page files.
- `--pdf` output PDF path (default: `<out>/<title>.pdf`).

Example:

```bash
python main.py https://fliphtml5.com/<pub>/<book>/Title \
  --out output \
  --workers 8 \
  --pdf output/book.pdf
```

## Notes

- Temporary folder `<out>/_pages` is always cleaned on success, failure, or cancel.
- `deString.js` is cached in `./.cache` for faster next runs.

## License

This project is licensed under the [MIT](LICENSE) License.
