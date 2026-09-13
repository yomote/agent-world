import importlib.util
import io
import os
import shutil
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

SCRIPT = Path(__file__).parents[1] / "azure" / "prepare_ghcr_image.py"
SPEC = importlib.util.spec_from_file_location("prepare_ghcr_image", SCRIPT)
assert SPEC and SPEC.loader
ghcr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ghcr)

DIGEST = "sha256:" + "a" * 64
ENV = {
    "GITHUB_ACTOR": "owner",
    "GH_TOKEN": "test-token",
    "SOURCE_SHA": "b" * 40,
    "IMAGE_REPOSITORY": "ghcr.io/owner/agent-world",
}


def bash_path(path: Path) -> str:
    value = path.resolve().as_posix()
    if os.name == "nt":
        drive, rest = value.split(":", 1)
        return f"/{drive.lower()}{rest}"
    return value


class Response:
    def __init__(self, body=b"", headers=None):
        self.body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


def result(args, *, stdout=""):
    return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")


def test_old_anonymous_probe_reproduces_first_package_403(tmp_path):
    """実run 34733341600と同じ匿名token 403で旧blockがdocker前にexit 22する。"""
    bash = (
        Path(os.environ.get("ProgramFiles", "")) / "Git" / "bin" / "bash.exe"
        if os.name == "nt"
        else Path(shutil.which("bash") or "")
    )
    assert bash.is_file()
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    trace = tmp_path / "trace"
    for name, body in {
        "curl": (
            '#!/usr/bin/env bash\necho curl >>"$TRACE"\necho "curl: (22) HTTP 403" >&2\nexit 22\n'
        ),
        "jq": "#!/usr/bin/env bash\ncat\n",
        "docker": '#!/usr/bin/env bash\necho docker >>"$TRACE"\n',
    }.items():
        executable = stub_bin / name
        executable.write_text(body, encoding="utf-8", newline="\n")
        executable.chmod(0o755)
    old_probe = """
set -euo pipefail
export PATH="${STUB_BIN}:$PATH"
registry_token="$(curl --fail-with-body --silent --show-error \
  "https://ghcr.io/token?service=ghcr.io&scope=repository:owner/agent-world:pull" | jq -r '.token')"
test -n "${registry_token}" && test "${registry_token}" != "null"
docker build --tag should-not-run .
"""

    completed = subprocess.run(
        [str(bash), "-c", old_probe],
        env={**os.environ, "STUB_BIN": bash_path(stub_bin), "TRACE": bash_path(trace)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 22
    assert "HTTP 403" in completed.stderr
    assert trace.read_text(encoding="utf-8").splitlines() == ["curl"]


def test_absent_package_uses_authenticated_probe_then_publishes_once(tmp_path):
    """匿名tokenが403になる初回packageでも、認証済み404だけを未作成として一度publishする。"""
    requests = []

    def open_url(request, timeout):
        requests.append(request)
        if "ghcr.io/token" in request.full_url:
            assert request.get_header("Authorization", "").startswith("Basic ")
            assert "scope=repository%3Aowner%2Fagent-world%3Apull%2Cpush" in request.full_url
            return Response(b'{"token":"registry-token"}')
        raise HTTPError(request.full_url, 404, "missing", {}, io.BytesIO())

    commands = []

    def run(command, **kwargs):
        commands.append((command, kwargs))
        if command[:3] == ["docker", "buildx", "imagetools"]:
            return result(command, stdout=DIGEST + "\n")
        return result(command)

    output = tmp_path / "github-output"
    reference = ghcr.prepare({**ENV, "GITHUB_OUTPUT": str(output)}, open_url, run)

    assert len(requests) == 2
    assert [item[0][1] for item in commands] == ["login", "build", "push", "buildx", "logout"]
    assert commands[0][1]["input"] == "test-token\n"
    assert reference == f"ghcr.io/owner/agent-world@{DIGEST}"
    assert output.read_text(encoding="utf-8") == f"reference={reference}\n"


def test_existing_manifest_reuses_digest_without_docker(tmp_path):
    """既存SHA tagをbuild/pushで上書きする回帰を防ぐ。"""
    responses = iter(
        [
            Response(b'{"token":"registry-token"}'),
            Response(headers={"Docker-Content-Digest": DIGEST}),
        ]
    )
    commands = []
    output = tmp_path / "github-output"

    reference = ghcr.prepare(
        {**ENV, "GITHUB_OUTPUT": str(output)},
        lambda request, timeout: next(responses),
        lambda command, **kwargs: commands.append(command),
    )

    assert reference.endswith(DIGEST)
    assert commands == []


def test_existing_manifest_without_digest_is_not_treated_as_absent(tmp_path):
    """成功応答の欠損headerを404扱いにして既存tagを上書きする回帰を防ぐ。"""
    responses = iter([Response(b'{"token":"registry-token"}'), Response()])
    commands = []

    with pytest.raises(RuntimeError, match="digest was invalid; image was not written"):
        ghcr.prepare(
            {**ENV, "GITHUB_OUTPUT": str(tmp_path / "github-output")},
            lambda request, timeout: next(responses),
            lambda command, **kwargs: commands.append(command),
        )

    assert commands == []


@pytest.mark.parametrize("failure", ["token-403", "manifest-403", "network"])
def test_auth_or_transport_failure_never_becomes_absent(failure, tmp_path):
    """認証拒否や通信不明を404へ丸め、imageを書き込む回帰を防ぐ。"""
    calls = 0

    def open_url(request, timeout):
        nonlocal calls
        calls += 1
        if failure == "token-403" or (failure == "manifest-403" and calls == 2):
            raise HTTPError(request.full_url, 403, "forbidden", {}, io.BytesIO())
        if failure == "network" and calls == 2:
            raise URLError("connection reset")
        return Response(b'{"token":"registry-token"}')

    commands = []
    with pytest.raises(RuntimeError, match="image was not written"):
        ghcr.prepare(
            {**ENV, "GITHUB_OUTPUT": str(tmp_path / "github-output")},
            open_url,
            lambda command, **kwargs: commands.append(command),
        )

    assert commands == []
