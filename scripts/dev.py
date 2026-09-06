"""初回セットアップと2プロセスの起動・終了。秘密や外部サービスは不要。"""

import hashlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from python_env import PYTHON, ROOT


def install_if_changed(source: Path, marker: Path, command: list[str]) -> None:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if not marker.exists() or marker.read_text() != digest:
        subprocess.run(command, cwd=ROOT, check=True)
        marker.write_text(digest)


def main() -> int:
    npm = shutil.which("npm")
    node = shutil.which("node")
    if not npm or not node:
        raise RuntimeError("Node.js 22.13以上とnpmをインストールしてください。")
    version = subprocess.check_output([node, "--version"], cwd=ROOT, text=True).strip()
    major, minor, *_ = map(int, version.lstrip("v").split("."))
    if major < 22 or (major == 22 and minor < 13):
        raise RuntimeError(f"Node.js 22.13以上が必要です（現在 {version}）。")
    for port in (8000, 5173):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as error:
                raise RuntimeError(
                    f"ポート{port}は使用中です。既存のサーバーを停止してください。"
                ) from error
    if not PYTHON.exists():
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
    install_if_changed(
        ROOT / "requirements-dev.txt",
        ROOT / ".venv/.sandbox-requirements",
        [str(PYTHON), "-m", "pip", "install", "-r", "requirements-dev.txt"],
    )
    install_if_changed(
        ROOT / "package-lock.json",
        ROOT / "node_modules/.sandbox-lock",
        [npm, "ci"],
    )
    commands = [
        [
            str(PYTHON),
            "-m",
            "uvicorn",
            "world.api:app",
            "--app-dir",
            "apps",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        [node, "node_modules/vite/bin/vite.js", "--config", "apps/web/vite.config.ts"],
    ]
    children: list[subprocess.Popen] = []
    try:
        for command in commands:
            children.append(subprocess.Popen(command, cwd=ROOT, start_new_session=os.name != "nt"))
        print("\nSandbox: http://127.0.0.1:5173  API: http://127.0.0.1:8000/docs", flush=True)
        print("停止: Ctrl+C。Pythonの変更は再起動すると反映されます。", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.25)
        raise RuntimeError("開発サーバーが終了しました。上のエラーログを確認してください。")
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=10)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
