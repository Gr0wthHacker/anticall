"""Local-processing guard: callbacks never send audio or text to a remote STT/LLM."""

from __future__ import annotations

from urllib.parse import urlsplit

from callshield.core.errors import PolicyBlockedError

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_loopback(host: str) -> bool:
    if host in _LOOPBACK_HOSTS:
        return True
    parts = host.split(".")
    return (
        len(parts) == 4 and parts[0] == "127" and all(p.isdigit() and int(p) < 256 for p in parts)
    )


def assert_local_only(model_endpoint: str) -> None:
    """Raise unless the endpoint is a loopback address or a unix socket path."""
    endpoint = (model_endpoint or "").strip()
    if endpoint.startswith("/"):
        return  # bare unix socket path
    try:
        parts = urlsplit(endpoint)
        host = (parts.hostname or "").lower()
    except ValueError:
        host = ""
    if parts.scheme == "unix" and not parts.netloc.strip("/"):
        return
    if parts.scheme in {"http+unix", "https+unix"} and parts.netloc.lower().startswith("%2f"):
        return
    if parts.scheme in {"http", "https", "ws", "wss", "grpc"} and _is_loopback(host):
        return
    raise PolicyBlockedError(
        "callbacks may only use local speech/language processing",
        code="CALLBACK_REMOTE_PROCESSING_BLOCKED",
    )
