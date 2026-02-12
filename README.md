# flipdl

Download FlipHTML5 pages and build a PDF from a public FlipHTML5 share URL.

## Features

- Accepts FlipHTML5 share URLs (e.g., `https://fliphtml5.com/<pub>/<book>/Title`).
- Downloads page images and builds a single PDF.
- Clean CLI output with progress bars.

## Requirements

- Python 3.10+

## Install

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python main.py https://fliphtml5.com/<pub>/<book>/Title
```

Common options:

```bash
python main.py https://fliphtml5.com/<pub>/<book>/Title \
  --out download \
  --workers 8
```

### Options

- `--out` output directory (default: `download`)
- `--workers` number of download workers
- `--overwrite` overwrite existing page files
- `--save-config` save parsed `config.json`
- `--pdf` output PDF path (default: `<out>/<title>.pdf`)
- `--keep-pages` keep downloaded page images

## Notes

- If the book uses encrypted `fliphtml5_pages`, the tool will download and cache `deString.js`
  then decode the embedded WASM directly from Python via `wasmtime`.
- Cached files are stored in `./.cache`.

## License

This project is licensed under the [MIT](LICENSE) License.
