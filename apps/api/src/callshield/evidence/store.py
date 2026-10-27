"""Artifact object store abstraction.

`LocalArtifactStore` keeps objects under a configurable root with strict key
validation (no traversal, no absolute paths, no hidden segments). `S3ArtifactStore`
is an interface stub only. Objects are write-once: overwriting an existing key is
refused so originals are never silently replaced.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MAX_KEY_LENGTH = 500


class UnsafeStorageKeyError(ValueError):
    """The storage key could escape the store root or is malformed."""


class ObjectNotFoundError(KeyError):
    """No object exists under the key (never deleted silently into success)."""


class ObjectExistsError(FileExistsError):
    """Write-once violation: the key already holds an object."""


class ArtifactStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> bool: ...

    def exists(self, key: str) -> bool: ...


def validate_storage_key(key: str) -> str:
    """Accept only `a/b/c` style keys of safe segments. Raises UnsafeStorageKeyError."""
    if not key or len(key) > MAX_KEY_LENGTH or "\\" in key or "\x00" in key or key.startswith("/"):
        raise UnsafeStorageKeyError("invalid storage key")
    for segment in key.split("/"):
        if segment in ("", ".", "..") or not _SEGMENT.match(segment):
            raise UnsafeStorageKeyError("invalid storage key segment")
    return key


class LocalArtifactStore:
    """Filesystem store rooted at `root` (created on demand)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        validate_storage_key(key)
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise UnsafeStorageKeyError("storage key escapes the store root")
        return candidate

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        if path.exists():
            raise ObjectExistsError("object already exists; originals are write-once")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            Path(tmp_name).replace(path)
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFoundError("object not found")
        return path.read_bytes()

    def delete(self, key: str) -> bool:
        """Remove the object. Returns False when it was already absent."""
        path = self._path(key)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class S3ArtifactStore:
    """Interface stub. A real implementation needs a configured bucket, server-side
    encryption and lifecycle rules; none ships in this release."""

    def __init__(self, bucket: str, prefix: str = "") -> None:
        self.bucket = bucket
        self.prefix = prefix

    def put(self, key: str, data: bytes) -> None:
        raise NotImplementedError("S3 artifact store is an interface stub")

    def get(self, key: str) -> bytes:
        raise NotImplementedError("S3 artifact store is an interface stub")

    def delete(self, key: str) -> bool:
        raise NotImplementedError("S3 artifact store is an interface stub")

    def exists(self, key: str) -> bool:
        raise NotImplementedError("S3 artifact store is an interface stub")


EVIDENCE_ROOT_ENV = "CALLSHIELD_EVIDENCE_ROOT"
_DEFAULT_ROOT = "./var/evidence"
_store: ArtifactStore | None = None


def get_artifact_store() -> ArtifactStore:
    """Process-wide store; root from CALLSHIELD_EVIDENCE_ROOT (default ./var/evidence)."""
    global _store
    if _store is None:
        _store = LocalArtifactStore(os.environ.get(EVIDENCE_ROOT_ENV, _DEFAULT_ROOT))
    return _store


def set_artifact_store(store: ArtifactStore | None) -> None:
    """Install (tests) or reset (None) the process-wide store."""
    global _store
    _store = store
