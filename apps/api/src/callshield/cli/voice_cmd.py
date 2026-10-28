"""`callshield voice models`: print recommendations; optional verified download."""

from __future__ import annotations

import argparse
import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx

from callshield.cli.context import CliContext
from callshield.cli.out import err, out
from callshield.configreg.ext_settings import VoiceSettings
from callshield.configreg.voice_catalog import CATALOG, VoiceModel, catalog_for


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("voice", help="voice engine helpers")
    inner = p.add_subparsers(dest="voice_command", required=True)
    models = inner.add_parser("models", help="recommended engines and models")
    models.add_argument("--download", metavar="ENGINE", help="download (needs a pinned sha256)")


def print_instructions(entry: VoiceModel, model_dir: str) -> None:
    out(f"- {entry.engine} ({entry.kind}): {entry.model}")
    out(f"    install:  pip install -e 'apps/api[{entry.pip_extra}]'")
    out(f"    get it:   {entry.page}")
    out(f"    place in: {Path(model_dir) / entry.engine}")
    out(f"    {entry.note}")


def _default_fetch(url: str) -> bytes:
    resp = httpx.get(url, timeout=120.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def download_model(
    entry: VoiceModel,
    dest_dir: Path,
    ctx: CliContext,
    fetch: Callable[[str], bytes] | None = None,
) -> int:
    if not entry.url:
        err(f"{entry.engine}: no direct file URL in the catalog. Download it manually (see above).")
        return 2
    if not entry.sha256:
        err(f"{entry.engine}: no pinned sha256 in the catalog, so it will NOT be auto-downloaded.")
        err(f"Download from {entry.page} and verify the publisher's checksum yourself.")
        return 2
    answer = ctx.input_fn(f"Download {entry.url} to {dest_dir}? Type 'download' to confirm: ")
    if answer.strip() != "download":
        err("cancelled")
        return 1
    data = (fetch or ctx.fetch or _default_fetch)(entry.url)
    if hashlib.sha256(data).hexdigest() != entry.sha256.lower():
        err("sha256 mismatch: file discarded")
        return 1
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / Path(entry.url).name).write_bytes(data)
    out(f"saved and verified: {dest_dir / Path(entry.url).name}")
    return 0


def run(args: argparse.Namespace, ctx: CliContext, catalog: Sequence[VoiceModel] = CATALOG) -> int:
    model_dir = VoiceSettings().model_dir
    if args.download:
        entry = catalog_for(args.download)
        if entry is None:
            err("unknown engine; known: " + ", ".join(e.engine for e in catalog))
            return 2
        return download_model(entry, Path(model_dir) / entry.engine, ctx)
    out("Recommended local voice engines (all UNVERIFIED suggestions; models are not bundled):")
    for entry in catalog:
        print_instructions(entry, model_dir)
    out("\nSet CALLSHIELD_VOICE_STT_ENGINE / _TTS_ENGINE; `callshield doctor` checks them.")
    return 0
