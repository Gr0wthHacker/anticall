"""Write files that may contain secrets: mode 0600 where the OS supports it."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path


def write_private_file(path: Path, content: str) -> None:
    """Create/overwrite ``path`` readable only by the owner (best effort on Windows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    with contextlib.suppress(OSError):  # Windows ignores most POSIX modes
        path.chmod(0o600)
