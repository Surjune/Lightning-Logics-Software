"""Write the API's OpenAPI document to a file, for frontend type generation."""

import json
import sys
import tempfile
from pathlib import Path

from app.core.config import Settings
from app.main import create_app


def main() -> None:
    out = Path(sys.argv[1])
    settings = Settings(
        cors_origins=[], audit_path=Path(tempfile.gettempdir()) / "openapi-export.jsonl", log_level="WARNING"
    )
    out.write_text(json.dumps(create_app(settings).openapi(), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
