"""Bounded read cache and disposable metadata for immutable analysis results.

Source signatures invalidate both caches. Sidecars are an optimization, never an
alternative source of measurements, and can be deleted without losing a run.
"""
from collections import OrderedDict
import json
from pathlib import Path
import threading

from app.storage import save_json
from app.settings import RUNTIME


_lock = threading.RLock()
_cache = OrderedDict()
_MAX_CACHED_RUNS = RUNTIME.run_cache_entries
_MAX_CACHED_BYTES = RUNTIME.run_cache_max_mb * 1024 * 1024


def signature(path):
    stat = Path(path).stat()
    return {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size, "inode": stat.st_ino}


def load_result(path, validate=None):
    """Return a shared read-only result; callers must copy before modifying it."""
    path = Path(path).resolve()
    with _lock:
        stamp = signature(path)
        cached = _cache.get(path)
        if cached and cached[0] == stamp:
            if validate and not cached[2]:
                validate(cached[1])
                _cache[path] = (cached[0], cached[1], True)
            _cache.move_to_end(path)
            return cached[1]
        run = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(run, dict):
            raise ValueError("Saved analysis must be an object")
        if validate:
            validate(run)
        # Do not cache an oversized run indefinitely. One normal real analysis
        # fits, and small test runs cannot cause unbounded process memory use.
        _cache.pop(path, None)
        if stamp["size"] <= _MAX_CACHED_BYTES:
            _cache[path] = (stamp, run, bool(validate))
            while len(_cache) > _MAX_CACHED_RUNS or sum(v[0]["size"] for v in _cache.values()) > _MAX_CACHED_BYTES:
                _cache.popitem(last=False)
        return run


def run_metadata(path):
    """Read a tiny source-bound index instead of reparsing every large result."""
    path = Path(path)
    stamp = signature(path)
    sidecar = path.with_name(".result-metadata.json")
    try:
        indexed = json.loads(sidecar.read_text(encoding="utf-8"))
        if indexed.get("source_signature") == stamp and isinstance(indexed.get("run"), dict):
            return indexed["run"]
    except (OSError, ValueError, AttributeError):
        pass
    run = load_result(path)
    metadata = {key: run.get(key) for key in ("id", "mode", "created_at", "name", "bbox")}
    try:
        with _lock:
            save_json(sidecar, {"source_signature": stamp, "run": metadata})
    except OSError:
        # Read-only run directories remain usable.
        pass
    return metadata


def clear_cache():
    with _lock:
        _cache.clear()
