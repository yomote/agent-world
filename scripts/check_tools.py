"""ローカル/CIで同じOSS検査を実行する。ツールの欠落は成功扱いしない。"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def executable(name: str) -> str:
    local = ROOT / "artifacts/tools" / name / (name + (".exe" if os.name == "nt" else ""))
    found = os.environ.get(name.upper() + "_BIN") or shutil.which(name)
    if found:
        return found
    if local.is_file():
        return str(local)
    sys.exit(f"{name}がありません。PATHまたは{name.upper()}_BINで指定してください。")


def main() -> None:
    if sys.argv[1:] == ["docs"]:
        subprocess.run(
            [
                executable("lychee"),
                "--config",
                ".lychee.toml",
                "*.md",
                "docs/**/*.md",
                ".github/**/*.md",
                "infra/**/*.md",
            ],
            cwd=ROOT,
            check=True,
        )
    elif sys.argv[1:] == ["iac"]:
        terraform = executable("terraform")
        for args in [
            ["fmt", "-check", "-recursive"],
            ["init", "-backend=false", "-input=false", "-lockfile=readonly"],
            ["validate"],
            ["test"],
        ]:
            subprocess.run([terraform, "-chdir=infra/github", *args], cwd=ROOT, check=True)
    else:
        sys.exit("引数はdocsまたはiacを指定してください。")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
