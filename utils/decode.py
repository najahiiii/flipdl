"""Decode helpers for FlipHTML5 encrypted page lists."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys

import aiohttp

try:
    from wasmtime import Linker, Module, Store, WasmtimeError
except ImportError:  # pragma: no cover - handled at runtime with clear error
    Linker = Module = Store = None

    class WasmtimeError(Exception):
        """Fallback error type when wasmtime is unavailable."""


DESTRING_URL = (
    "https://static.fliphtml5.com/resourceFiles/html5_templates/js/deString.js"
)
DESTRING_WASM_RE = re.compile(r"data:application/octet-stream;base64,([A-Za-z0-9+/=]+)")
WASM_PAGE_SIZE = 65536

_RUNTIME_CACHE: dict[str, "_DeStringRuntime"] = {}


def _decode_with_runtime(js_path: str, value: str) -> str:
    runtime = get_runtime(js_path)
    return runtime.decode(value)


async def decode_pages(pages_raw, session: aiohttp.ClientSession) -> list | None:
    """Return page list from raw config value, decoding if needed."""
    if isinstance(pages_raw, list):
        return pages_raw
    if isinstance(pages_raw, str):
        text = pages_raw.strip()
        if text.startswith("[") or text.startswith("{"):
            return parse_pages_json(text)
        decoded = await destring(text, session)
        if not decoded:
            return None
        return parse_pages_json(decoded)
    return None


def parse_pages_json(text: str) -> list | None:
    """Parse a JSON array, tolerating extra prefix/suffix text."""
    raw = text.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, list) else None


async def ensure_destring_js(session: aiohttp.ClientSession) -> str:
    """Download and cache deString.js for decoding."""
    cache_dir = os.path.join(os.getcwd(), ".cache")
    os.makedirs(cache_dir, exist_ok=True)
    js_path = os.path.join(cache_dir, "deString.js")
    if os.path.exists(js_path) and os.path.getsize(js_path) > 0:
        return js_path
    async with session.get(DESTRING_URL) as resp:
        resp.raise_for_status()
        content = await resp.read()
    with open(js_path, "wb") as f:
        f.write(content)
    return js_path


def extract_wasm_from_js(js_path: str) -> bytes:
    """Extract embedded WASM bytes from deString.js."""
    with open(js_path, "r", encoding="utf-8", errors="replace") as f:
        code = f.read()
    match = DESTRING_WASM_RE.search(code)
    if not match:
        raise RuntimeError("failed to find embedded deString wasm binary")
    return base64.b64decode(match.group(1))


def get_runtime(js_path: str) -> "_DeStringRuntime":
    """Return cached runtime for a deString.js path."""
    runtime = _RUNTIME_CACHE.get(js_path)
    if runtime is not None:
        return runtime
    if Module is None or Store is None or Linker is None:
        raise RuntimeError(
            "python package 'wasmtime' is required to decode fliphtml5_pages"
        )
    wasm_bytes = extract_wasm_from_js(js_path)
    runtime = _DeStringRuntime(wasm_bytes)
    if not runtime.is_ready():
        raise RuntimeError("failed to initialize deString runtime")
    _RUNTIME_CACHE[js_path] = runtime
    return runtime


class _DeStringRuntime:
    """Minimal WASM runtime wrapper for FlipHTML5 DeString."""

    def __init__(self, wasm_bytes: bytes) -> None:
        store = Store()
        module = Module(store.engine, wasm_bytes)
        linker = Linker(store.engine)
        self._define_imports(linker, module)
        instance = linker.instantiate(store, module)
        exports = instance.exports(store)

        self._store = store
        self._memory = exports["memory"]
        self._malloc = exports["malloc"]
        self._free = exports["free"]
        self._destring = exports["DeString"]

        exports["emscripten_stack_init"](store)
        exports["__wasm_call_ctors"](store)

    def is_ready(self) -> bool:
        """Return True when required exports are resolved."""
        return all((self._memory, self._malloc, self._free, self._destring))

    def _import_type(self, wasm_module, import_module: str, name: str):
        for imp in wasm_module.imports:
            if imp.module == import_module and imp.name == name:
                return imp.type
        raise RuntimeError(f"missing wasm import: {import_module}.{name}")

    def _define_imports(self, linker, wasm_module) -> None:
        linker.define_func(
            "env",
            "emscripten_run_script",
            self._import_type(wasm_module, "env", "emscripten_run_script"),
            self._emscripten_run_script,
            access_caller=True,
        )
        linker.define_func(
            "env",
            "emscripten_memcpy_big",
            self._import_type(wasm_module, "env", "emscripten_memcpy_big"),
            self._emscripten_memcpy_big,
            access_caller=True,
        )
        linker.define_func(
            "wasi_snapshot_preview1",
            "fd_write",
            self._import_type(wasm_module, "wasi_snapshot_preview1", "fd_write"),
            self._fd_write,
            access_caller=True,
        )
        linker.define_func(
            "env",
            "emscripten_resize_heap",
            self._import_type(wasm_module, "env", "emscripten_resize_heap"),
            self._emscripten_resize_heap,
            access_caller=True,
        )

    def _caller_memory(self, caller):
        memory = caller.get("memory")
        if memory is None:
            raise RuntimeError("wasm memory export is unavailable")
        return memory

    def _emscripten_run_script(self, _caller, _script_ptr: int) -> None:
        # DeString does not depend on JS eval side effects for our usage.
        return None

    def _emscripten_memcpy_big(self, caller, dest: int, src: int, size: int) -> None:
        if size <= 0:
            return None
        memory = self._caller_memory(caller)
        chunk = bytes(memory.read(caller, src, src + size))
        memory.write(caller, chunk, dest)
        return None

    def _fd_write(
        self, caller, _fd: int, iovs: int, iovs_len: int, nwritten: int
    ) -> int:
        # wasm writes informational text to stdout/stderr; we ignore content.
        memory = self._caller_memory(caller)
        total = 0
        for i in range(iovs_len):
            base = iovs + i * 8
            chunk_len = int.from_bytes(
                memory.read(caller, base + 4, base + 8),
                "little",
            )
            total += chunk_len
        memory.write(caller, total.to_bytes(4, "little"), nwritten)
        return 0

    def _emscripten_resize_heap(self, caller, requested_size: int) -> int:
        memory = self._caller_memory(caller)
        current_size = memory.data_len(caller)
        if requested_size <= current_size:
            return 1
        pages_to_grow = (requested_size - current_size + WASM_PAGE_SIZE - 1) // (
            WASM_PAGE_SIZE
        )
        try:
            memory.grow(caller, pages_to_grow)
            return 1
        except WasmtimeError:
            return 0

    def decode(self, value: str) -> str:
        """Decode encrypted value and return raw decoded text."""
        input_bytes = value.encode("utf-8") + b"\x00"
        input_ptr = self._malloc(self._store, len(input_bytes))
        try:
            self._memory.write(self._store, input_bytes, input_ptr)
            output_ptr = self._destring(self._store, input_ptr)
            return self._read_c_string(output_ptr)
        finally:
            self._free(self._store, input_ptr)

    def _read_c_string(self, pointer: int) -> str:
        if pointer <= 0:
            return ""
        mem_len = self._memory.data_len(self._store)
        if pointer >= mem_len:
            return ""
        raw = bytes(self._memory.read(self._store, pointer, mem_len))
        nul = raw.find(b"\x00")
        if nul >= 0:
            raw = raw[:nul]
        return raw.decode("utf-8", errors="replace")


async def destring(value: str, session: aiohttp.ClientSession) -> str | None:
    """Decode encrypted text using FlipHTML5 deString WASM from Python."""
    try:
        js_path = await ensure_destring_js(session)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _decode_with_runtime, js_path, value)
    except (
        aiohttp.ClientError,
        asyncio.TimeoutError,
        OSError,
        RuntimeError,
        WasmtimeError,
    ) as exc:
        print(f"error: destring failed: {exc}", file=sys.stderr)
        return None
