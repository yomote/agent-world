"""OSに応じてリポジトリ専用venvのPythonでコマンドを実行する。"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

if __name__ == "__main__":
    if not PYTHON.exists():
        sys.exit("venvがありません。最初に python scripts/dev.py を実行してください。")
    sys.exit(subprocess.call([str(PYTHON), *sys.argv[1:]], cwd=ROOT))
