"""The single output layer of the CLI. Nothing else in the package may print."""

from __future__ import annotations

import sys


def out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def err(text: str = "") -> None:
    sys.stderr.write(text + "\n")
