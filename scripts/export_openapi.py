"""Pydantic/FastAPIからOpenAPIを生成。手書きの二重定義を避ける。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps"))

from world.api import create_app  # noqa: E402

target = ROOT / "docs/api/openapi.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n", "utf-8")
