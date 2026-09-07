"""native backendの実送信上限と結果不明・head束縛をネットワークなしで検証する。"""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))
from scripts.automation import delivery  # noqa: E402
from scripts.automation import github_adapter as adapter
from scripts.automation.runner import Runner, dispatcher  # noqa: E402
from scripts.automation.transport import Stop  # noqa: E402

HEAD = "a" * 40
OWNER = "01a07c68-367d-75e3-b267-3ae46db963ac"


@pytest.fixture
def task(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "existing_gcm", lambda _: "test-only-auth")
    with dispatcher(tmp_path):
        runner = Runner(tmp_path)
        task = delivery.Campaign(runner, "runner")
        task.init(OWNER, "b" * 40)
        task.enter()
        yield task
        runner.close()


class Response:
    def __init__(self, status=200, data=None, headers=None):
        self.status, self.data, self.headers = status, data or {}, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, count):
        return json.dumps(self.data).encode()


def test_thirty_requests_shared_across_restart_and_campaign(task, monkeypatch):
    """31件目を送信し、resumeやhelper campaignでHTTP予算をresetする回帰を防ぐ。"""
    sent = []

    def send(request, **kwargs):
        sent.append(request.full_url)
        return Response(data={"protected": True})

    monkeypatch.setattr(task.transport.github.opener, "open", send)
    for _ in range(30):
        task.transport.github.call("main_protection", {})
    successor = adapter.GitHub(task)
    monkeypatch.setattr(successor.opener, "open", send)
    with pytest.raises(Stop, match="http_request_budget_30"):
        successor.call("main_protection", {})
    child = delivery.Campaign(
        type("RunnerRef", (), {"db": task.db, "root": task.root})(), "billing-helper"
    )
    child.init(OWNER, HEAD)
    assert child.data()["http_requests"] == 30
    assert len(sent) == 30


@pytest.mark.parametrize(
    "operation,method,path",
    [
        (
            "workflow_dispatch",
            "POST",
            adapter.PREFIX + "/actions/workflows/merge-gate.yml/dispatches",
        ),
        ("normal_merge", "PUT", "/repos/other/repo/pulls/1/merge"),
        ("normal_merge", "GET", adapter.PREFIX + "/branches/main"),
        ("main_protection", "GET", "https://elsewhere.invalid"),
    ],
)
def test_host_repo_operation_are_fixed_before_auth_or_send(task, operation, method, path):
    """dispatch・別repo・operationとmethodの不一致をallowlistから漏らす回帰を防ぐ。"""
    with pytest.raises(Stop, match="not_allowed"):
        task.transport.github.request(operation, method, path)
    assert task.data()["http_requests"] == 0


def test_known_403_stops_and_never_falls_back(task, monkeypatch):
    """既知403でconnectorや別tokenへ切り替えて再送する回帰を防ぐ。"""
    calls = []

    def send(*args, **kwargs):
        calls.append(1)
        return Response(403)

    monkeypatch.setattr(task.transport.github.opener, "open", send)
    with pytest.raises(Stop, match="github_http_403") as caught:
        task.transport.call("main_protection", {})
    task.finish_step(caught.value.state, caught.value.reason)
    with pytest.raises(Stop):
        task.enter()
    assert len(calls) == 1
    assert task.data()["http_requests"] == 1
    assert "test-only-auth" not in (task.directory / "status.json").read_text()


def test_unknown_write_consumes_request_and_is_not_retried(task, monkeypatch):
    """write timeoutを未送信として再試行・予算返金する回帰を防ぐ。"""
    calls = []

    def send(*args, **kwargs):
        calls.append(1)
        raise TimeoutError()

    monkeypatch.setattr(task.transport.github.opener, "open", send)
    with pytest.raises(Stop, match="transport_no_retry") as caught:
        task.transport.call("create_helper_issue", {"title": "fixed", "body": "data"}, write=True)
    task.finish_step(caught.value.state, caught.value.reason)
    with pytest.raises(Stop):
        task.enter()
    assert len(calls) == 1 and task.data()["http_requests"] == 1


def test_merge_requires_joined_current_evidence_and_sends_expected_sha_once(task, monkeypatch):
    """旧headや欠けたcurrent checkをmerge packetへ流用する回帰を防ぐ。"""
    arguments = {"pr_number": 25, "expected_head_sha": HEAD}
    with pytest.raises(Stop, match="merge_packet_not_bound"):
        task.transport.github.call("normal_merge", arguments)
    assert task.data()["http_requests"] == 0
    data = task.data()
    data.update(
        pr=25,
        merge_validated_head=HEAD,
        merge_validated_at=time.time(),
        review={"head": HEAD, "verdict": "pass", "reviewer": delivery.REVIEWER},
        ci={"head_sha": HEAD, "pr_number": 25, "conclusion": "success", "check_job_id": 7},
        current_check={"head": HEAD, "end_head": HEAD, "exit_code": 0, "clean": True},
        protection={"protected": True},
        merge_snapshot={
            "pr": {
                "number": 25,
                "state": "open",
                "draft": False,
                "mergeable": True,
                "head": {"sha": HEAD, "repo": {"full_name": adapter.REPOSITORY}},
                "base": {"ref": "main", "repo": {"full_name": adapter.REPOSITORY}},
            },
            "comments": [
                {
                    "author_association": "OWNER",
                    "body": f"<!-- agent-world-independent-review -->\nhead: {HEAD}\n"
                    f"verdict: pass\nreviewer: {delivery.REVIEWER}\n",
                }
            ],
            "threads": [],
        },
    )
    task.save_event(data, "test_validated")
    sent = []

    def send(request, **kwargs):
        sent.append(json.loads(request.data))
        return Response(data={"merged": True, "sha": "c" * 40})

    monkeypatch.setattr(task.transport.github.opener, "open", send)
    assert task.transport.github.call("normal_merge", arguments)["merged"] is True
    with pytest.raises(Stop, match="already_attempted"):
        task.transport.github.call("normal_merge", arguments)
    assert sent == [{"sha": HEAD, "merge_method": "squash"}]


def test_304_and_redirect_each_consume_budget_without_following(task, monkeypatch):
    """304を無課金の照会と数え、redirect先を無制限に追う回帰を防ぐ。"""
    responses = iter(
        [
            Response(data={"protected": True}, headers={"ETag": "fixed"}),
            Response(304),
            Response(302),
        ]
    )
    sent = []

    def send(request, **kwargs):
        sent.append(request)
        return next(responses)

    monkeypatch.setattr(task.transport.github.opener, "open", send)
    assert task.transport.github.call("main_protection", {})["protected"] is True
    assert task.transport.github.call("main_protection", {})["protected"] is True
    assert sent[-1].get_header("If-none-match") == "fixed"
    with pytest.raises(Stop, match="github_http_302"):
        task.transport.github.call("main_protection", {})
    assert task.data()["http_requests"] == len(sent) == 3
