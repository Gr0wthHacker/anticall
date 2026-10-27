"""Export the OpenAPI spec to apps/api/openapi.json."""

from __future__ import annotations

import json
from pathlib import Path

from callshield.api.app import create_app
from callshield.core.config import Settings


def main() -> None:
    app = create_app(Settings(db_url="sqlite://", environment="test"))
    target = Path(__file__).resolve().parent.parent / "openapi.json"
    target.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
