"""Regenerate (--write) or verify (--check) the config reference doc and env.example."""

from __future__ import annotations

import argparse
import sys

from callshield.configreg.docgen import (
    build_reference,
    env_example_path,
    reference_path,
    stale_files,
)
from callshield.providers.render_env import render_env_example


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        reference_path().write_text(build_reference(), encoding="utf-8", newline="\n")
        env_example_path().write_text(render_env_example(), encoding="utf-8", newline="\n")
        sys.stdout.write("wrote configuration-reference.md and env.example\n")
        return 0
    stale = stale_files()
    for path in stale:
        sys.stderr.write(f"stale: {path}\n")
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
