"""既存factoryのtimeout処理でclean current headのcheckを実行する。"""

import argparse
import json
import shutil
from pathlib import Path

from scripts.factory import run_command

from .delivery import git
from .transport import atomic_json


def check(workspace, head, directory):
    if git(workspace, "rev-parse", "HEAD") != head or git(workspace, "status", "--porcelain"):
        raise ValueError("current_head_not_clean")
    npm = shutil.which("npm")
    if npm is None:
        raise ValueError("npm_missing")
    result = run_command([npm, "run", "check"], workspace, directory / "current-check.log", 180)
    result.update(
        head=head,
        end_head=git(workspace, "rev-parse", "HEAD"),
        clean=not git(workspace, "status", "--porcelain"),
    )
    atomic_json(directory / "current-check.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("head")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.workspace, args.head, args.directory)))


if __name__ == "__main__":
    main()
