"""Pydantic/FastAPIからOpenAPIを生成。手書きの二重定義を避ける。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps"))

from accounting_agent.api import create_app as create_accounting_app  # noqa: E402
from world.api import create_app  # noqa: E402

target = ROOT / "docs/api/openapi.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n", "utf-8")

accounting_target = ROOT / "docs/api/accounting-openapi.json"
accounting_target.write_text(
    json.dumps(create_accounting_app(Path(":memory:")).openapi(), ensure_ascii=False, indent=2)
    + "\n",
    "utf-8",
)
