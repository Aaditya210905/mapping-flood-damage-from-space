"""Small file utilities shared by satellite acquisition and reference evaluation."""
import hashlib
import json
from pathlib import Path

import httpx
from app.settings import ACQUISITION

ROOT = Path(__file__).resolve().parent.parent


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def download(url, path, limit=64 * 1024 * 1024):
    """Download a bounded reference file atomically; leave complete files alone."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    temporary = path.with_suffix(path.suffix + ".part")
    try:
        with httpx.Client(timeout=ACQUISITION.reference_download_timeout_seconds, follow_redirects=True) as client, client.stream("GET", url) as response:
            response.raise_for_status()
            written = 0
            with temporary.open("wb") as stream:
                for block in response.iter_bytes():
                    written += len(block)
                    if written > limit:
                        raise ValueError(f"Download exceeds its {limit}-byte limit: {path.name}")
                    stream.write(block)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
