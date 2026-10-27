"""Small object-store interface for post-disclosure audio/transcripts (outbound_recording).

Local filesystem writer only; evidence-store integration is out of scope. Objects are
addressed by an opaque key and hashed (sha256) at write time.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Protocol

_SAFE_KEY = re.compile(r"^[A-Za-z0-9._\-/]{1,180}$")


class MediaStore(Protocol):
    def put(self, key: str, data: bytes) -> str:
        """Store bytes, return their sha256 hex."""
        ...

    def delete(self, key: str) -> bool: ...

    def exists(self, key: str) -> bool: ...


class LocalFsStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        if not _SAFE_KEY.match(key) or ".." in key:
            raise ValueError("unsafe storage key")
        return self._root / key

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if path.exists():
            path.unlink()
            return True
        return False

    def exists(self, key: str) -> bool:
        return self._path(key).exists()
