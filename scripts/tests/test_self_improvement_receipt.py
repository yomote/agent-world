"""PR30の別開発証拠だけを読み、旧proof状態を成功へ書き換えない。"""

import copy
import json
import sqlite3
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))
from scripts.automation import delivery, helper_job  # noqa: E402
from scripts.automation.delivery import SCOPES  # noqa: E402
from scripts.automation.github_adapter import GitHub  # noqa: E402
from scripts.automation.runner import digest  # noqa: E402
from scripts.automation.transport import Stop  # noqa: E402

RECEIPT = "artifacts/self-improvement/runtime-development/result.json"
FIXTURE = Path(__file__).with_name("fixtures") / "self_improvement_pr30.json"


@pytest.fixture
def saved(tmp_path, monkeypatch):
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    proof = tmp_path / "artifacts/self-improvement/state.sqlite3"
    proof.parent.mkdir(parents=True)
    with sqlite3.connect(proof) as db:
        db.executescript("""
            CREATE TABLE delivery_campaigns(name TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE delivery_attempts(campaign TEXT,attempt INTEGER,data TEXT,
                frozen INTEGER,evidence TEXT,PRIMARY KEY(campaign,attempt));
            CREATE TABLE delivery_events(seq INTEGER PRIMARY KEY,campaign TEXT,at REAL,
                kind TEXT,data TEXT);
            CREATE TABLE delivery_http_budget(id INTEGER PRIMARY KEY,used INTEGER);
            INSERT INTO delivery_http_budget VALUES(1,20);
        """)
        db.executemany(
            "INSERT INTO delivery_campaigns VALUES (?,?)",
            [
                ("runner", json.dumps(value["parent"])),
                ("billing-helper", json.dumps(value["helper"])),
            ],
        )
        db.executemany(
            "INSERT INTO delivery_attempts VALUES ('billing-helper',?,?,?,?)",
            value["frozen_attempts"],
        )
        db.execute(
            "INSERT INTO delivery_events VALUES(1,'billing-helper',?,'initialized','{}')",
            (value["helper"]["prior_started_at"],),
        )
    development = tmp_path / "artifacts/self-improvement/runtime-development/ledger.sqlite3"
    development.parent.mkdir()
    with sqlite3.connect(development) as db:
        db.executescript("""
            CREATE TABLE delivery_campaigns(name TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE delivery_operations(id TEXT,campaign TEXT,operation TEXT,state TEXT,
                request TEXT,response TEXT);
            CREATE TABLE delivery_http_budget(id INTEGER PRIMARY KEY,used INTEGER);
            CREATE TABLE delivery_http_requests(operation TEXT,method TEXT,path TEXT,
                state TEXT,status INTEGER);
            INSERT INTO delivery_http_budget VALUES(1,9);
        """)
        db.execute(
            "INSERT INTO delivery_campaigns VALUES ('runtime-development',?)",
            (json.dumps(value["development"]),),
        )
        operation = value["merge_operation"]
        db.execute(
            "INSERT INTO delivery_operations VALUES (?,'runtime-development',?,?,?,?)", operation
        )
        db.execute("INSERT INTO delivery_http_requests VALUES (?,?,?,?,?)", value["merge_http"])
    (tmp_path / RECEIPT).write_text(
        json.dumps(
            {
                "development": value["development"],
                "proof_after": value["development"]["proof_before"],
                "proof_unchanged": True,
            }
        ),
        encoding="utf-8",
    )
    # fixtureではCLI log本文を公開しない。
    # 既存file/log検査は既存専用testと実preflightで確認する。
    directory = tmp_path / "artifacts/self-improvement/billing-helper/attempt-2"
    directory.mkdir(parents=True)
    (directory / "cli-2.jsonl").write_bytes(b"sanitized fixture log")
    monkeypatch.setattr(
        helper_job,
        "digest",
        lambda data: helper_job.ATTEMPT2_LOG if data == b"sanitized fixture log" else digest(data),
    )
    workspace = directory / "checkout"
    for path in SCOPES["billing-helper"]:
        target = workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"sanitized fixture file")
    monkeypatch.setattr(helper_job, "ATTEMPT2_HASHES", helper_job.hashes(workspace))

    def git(workspace, *args):
        if args[0] == "rev-parse":
            return (
                value["development"]["merge_sha"]
                if args[1] == "origin/main"
                else value["helper"]["base"]
            )
        if args[0] == "ls-files":
            return "\n".join(SCOPES["billing-helper"])
        return ""

    monkeypatch.setattr(helper_job, "git", git)
    return tmp_path, proof, development, value


def contents(path):
    with sqlite3.connect(path) as db:
        return "\n".join(db.iterdump())


def test_real_pr30_state_fixture_passes_preflight_without_mutation(saved):
    """実PR30/failed attempt2の秘密除外fixtureで、parent偽装・attempt/deadline生成を防ぐ。"""
    root, proof, development, value = saved
    before = (contents(proof), contents(development))
    result = helper_job.preflight_development_receipt(root, Path(RECEIPT))
    assert result["status"] == "preflight_pass"
    assert result["development_head"] == value["development"]["head"]
    assert result["development_merge"] == value["development"]["merge_sha"]
    assert result["issue"] == 28 and result["duplicate_key"] == helper_job.DUPLICATE
    assert result["http_requests"] == 20 and result["cli_starts"] == 2
    assert result["attempt_created"] is False and result["deadline_created"] is False
    assert result["attempt_creation_authorized"] is False
    assert before == (contents(proof), contents(development))
    assert not (root / "artifacts/self-improvement/billing-helper/attempt-3").exists()


def test_later_normal_merge_receipt_becomes_helper_base(saved, monkeypatch):
    """旧開発番号やSHAへ固定して、是正後のlatest mainからhelperを起動できない回帰を防ぐ。"""
    root, proof, development, value = saved
    proof_before = contents(proof)
    state = copy.deepcopy(value["development"])
    state.update(
        name="receipt-development",
        base=value["development"]["merge_sha"],
        head="c" * 40,
        merge_sha="d" * 40,
        pr=31,
    )
    state["review"].update(head=state["head"], base=state["base"], reviewer="/root/reviewer")
    state["current_check"].update(head=state["head"], end_head=state["head"])
    state["ci"].update(head_sha=state["head"], pr_number=state["pr"])
    request = {
        "id": "later-merge",
        "campaign": state["name"],
        "operation": "normal_merge",
        "arguments": {
            "repository_full_name": delivery.REPOSITORY,
            "pr_number": state["pr"],
            "expected_head_sha": state["head"],
            "merge_method": "squash",
        },
        "write": True,
    }
    response = {
        "id": "later-merge",
        "status": "ok",
        "result": {"merged": True, "sha": state["merge_sha"]},
    }
    with sqlite3.connect(development) as db:
        db.execute(
            "UPDATE delivery_campaigns SET name=?,data=?", (state["name"], json.dumps(state))
        )
        db.execute(
            "UPDATE delivery_operations SET id=?,campaign=?,request=?,response=?",
            ("later-merge", state["name"], json.dumps(request), json.dumps(response)),
        )
        db.execute(
            "UPDATE delivery_http_requests SET path=?",
            (f"/repos/yomote/agent-world/pulls/{state['pr']}/merge",),
        )
    (root / RECEIPT).write_text(
        json.dumps(
            {
                "development": state,
                "proof_after": state["proof_before"],
                "proof_unchanged": True,
            }
        ),
        encoding="utf-8",
    )
    fixture_git = helper_job.git
    monkeypatch.setattr(
        helper_job,
        "git",
        lambda workspace, *args: (
            state["merge_sha"]
            if args[:2] == ("rev-parse", "origin/main")
            else fixture_git(workspace, *args)
        ),
    )

    result = helper_job.preflight_development_receipt(root, Path(RECEIPT))
    assert result["development_head"] == state["head"]
    assert result["development_merge"] == state["merge_sha"]
    assert contents(proof) == proof_before


@pytest.mark.parametrize(
    "change",
    [
        "receipt_head",
        "joint_head",
        "merge",
        "purpose",
        "review",
        "ci",
        "protection",
        "unknown_merge",
        "merge_http",
        "http",
        "cli",
        "duplicate",
        "old_deadline",
        "parent",
    ],
)
def test_mismatch_or_changed_proof_is_rejected_without_mutation(saved, change):
    """一致しないreceipt、両方偽装した任意head、旧予算/期限/key変更を拒否する。"""
    root, proof, development, value = saved
    receipt = json.loads((root / RECEIPT).read_text())
    state = copy.deepcopy(value["development"])
    if change == "receipt_head":
        receipt["development"]["head"] = "a" * 40
    elif change in {"joint_head", "merge", "purpose", "review", "ci", "protection"}:
        if change == "joint_head":
            state["head"] = "a" * 40
        elif change == "merge":
            state["merge_sha"] = "b" * 40
        elif change == "purpose":
            state["purpose"] = "proof"
        elif change == "review":
            state["review"]["verdict"] = "fail"
        elif change == "ci":
            state["ci"]["head_sha"] = "a" * 40
        else:
            state["protection"]["protected"] = False
        receipt["development"] = state
        with sqlite3.connect(development) as db:
            db.execute("UPDATE delivery_campaigns SET data=?", (json.dumps(state),))
    elif change in {"unknown_merge", "merge_http"}:
        with sqlite3.connect(development) as db:
            if change == "unknown_merge":
                db.execute("UPDATE delivery_operations SET state='unknown'")
            else:
                db.execute("UPDATE delivery_http_requests SET status=403")
    else:
        with sqlite3.connect(proof) as db:
            if change == "http":
                db.execute("UPDATE delivery_http_budget SET used=11")
            else:
                name = "runner" if change == "parent" else "billing-helper"
                data = json.loads(
                    db.execute(
                        "SELECT data FROM delivery_campaigns WHERE name=?", (name,)
                    ).fetchone()[0]
                )
                key, item = {
                    "cli": ("cli_starts", 1),
                    "duplicate": ("duplicate_key", "different"),
                    "old_deadline": ("prior_deadline", 1788802386.6396668),
                    "parent": ("attempt_id", 3),
                }[change]
                data[key] = item
                db.execute(
                    "UPDATE delivery_campaigns SET data=? WHERE name=?", (json.dumps(data), name)
                )
    (root / RECEIPT).write_text(json.dumps(receipt), encoding="utf-8")
    before = (contents(proof), contents(development))
    with pytest.raises(Stop):
        helper_job.preflight_development_receipt(root, Path(RECEIPT))
    assert before == (contents(proof), contents(development))


def test_only_fixed_receipt_input_is_accepted(saved):
    """任意receiptや新しい回復packetを入力にできない。"""
    root, proof, development, _ = saved
    other = root / "other.json"
    other.write_text((root / RECEIPT).read_text())
    before = (contents(proof), contents(development))
    with pytest.raises(Stop):
        helper_job.preflight_development_receipt(root, other)
    assert before == (contents(proof), contents(development))


def test_public_preflight_never_constructs_a_mutable_runner(saved, monkeypatch, capsys):
    """公開commandがschema作成や旧proof更新を伴うRunner入口へ落ちる回帰を防ぐ。"""
    root, proof, development, _ = saved
    before = (contents(proof), contents(development))
    monkeypatch.setattr(delivery, "ROOT", root)
    monkeypatch.setattr(delivery, "Runner", lambda *args: pytest.fail("mutable Runner forbidden"))
    monkeypatch.setattr(delivery, "dispatcher", lambda *args: pytest.fail("preflight only reads"))
    monkeypatch.setattr(
        sys, "argv", ["delivery", "billing-helper", "preflight", "--development-receipt", RECEIPT]
    )
    assert delivery.main() == 0
    assert json.loads(capsys.readouterr().out)["attempt_created"] is False
    assert before == (contents(proof), contents(development))


@pytest.fixture
def creation(saved, monkeypatch):
    root, proof, development, value = saved
    packet = "artifacts/self-improvement/validation-attempt-3.json"
    authorization = {
        **value["helper"]["validation_packet"],
        "kind": "issue28-runtime-validation-v1",
        "corrective_head": value["development"]["head"],
        "corrective_merge": value["development"]["merge_sha"],
        "runtime_profile": helper_job.RUNTIME_PROFILE,
    }
    (root / packet).write_text(json.dumps(authorization), encoding="utf-8")
    # 旧79のraw commit検査は既存専用testが担当。ここでは入力境界と永続遷移を確認する。
    monkeypatch.setattr(helper_job, "committed_files", lambda *args: None)
    monkeypatch.setattr(delivery, "ROOT", root)
    monkeypatch.setattr(delivery, "dispatcher", lambda *args: nullcontext())
    monkeypatch.setattr(helper_job, "run_job", lambda *a, **k: pytest.fail("child forbidden"))
    monkeypatch.setattr(delivery.Transport, "call", lambda *a, **k: pytest.fail("HTTP forbidden"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "delivery",
            "billing-helper",
            "new-helper-attempt",
            "--packet",
            packet,
            "--development-receipt",
            RECEIPT,
        ],
    )
    return root, proof, development, value


def test_creation_entry_dry_run_cannot_write_or_allocate_deadline(creation, monkeypatch, capsys):
    """実作成と同じpacket検査を通しても、dry-runはDB/child/deadlineを生成しない。"""
    root, proof, development, _ = creation
    before = (contents(proof), contents(development))
    monkeypatch.setattr(delivery, "Runner", lambda *args: pytest.fail("mutable Runner forbidden"))
    monkeypatch.setattr(delivery, "dispatcher", lambda *args: pytest.fail("read-only only"))
    monkeypatch.setattr(
        delivery.Campaign, "new_attempt", lambda *args: pytest.fail("attempt creation forbidden")
    )
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--dry-run"])
    assert delivery.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "attempt3_dry_run_pass"
    assert result["base"] == creation[3]["development"]["merge_sha"]
    assert result["http_requests"] == 20 and result["cli_starts"] == 2
    assert all(
        result[key] is False
        for key in (
            "attempt_created",
            "deadline_created",
            "child_started",
            "attempt_creation_authorized",
        )
    )
    assert not {"started_at", "deadline"} & result.keys()
    assert before == (contents(proof), contents(development))
    assert not (root / "artifacts/self-improvement/billing-helper/attempt-3").exists()


def test_explicit_creation_entry_reuses_verified_receipt_without_rewriting_parent(creation, capsys):
    """fixture DBの正規作成入口でだけ新900秒を保存し、旧2試行と全予算を保持する。"""
    root, proof, development, value = creation
    with sqlite3.connect(proof) as db:
        parent = db.execute("SELECT data FROM delivery_campaigns WHERE name='runner'").fetchone()
        attempts = db.execute("SELECT * FROM delivery_attempts ORDER BY attempt").fetchall()
        events = db.execute("SELECT * FROM delivery_events").fetchall()
    development_before = contents(development)
    assert delivery.main() == 0
    data = json.loads(capsys.readouterr().out)
    assert data["attempt_id"] == 3 and data["state"] == "ready"
    assert data["base"] == value["development"]["merge_sha"]
    assert data["development_parent"]["head"] == value["development"]["head"]
    assert data["head"] is None and data["job_owner"] is None
    assert data["deadline"] == data["started_at"] + 900
    assert data["prior_started_at"] == value["helper"]["started_at"]
    assert data["prior_deadline"] == value["helper"]["deadline"]
    for key in (
        "http_requests",
        "max_http_requests",
        "cli_starts",
        "max_cli_starts",
        "connector_calls",
        "max_connector_calls",
        "delivery_attempts",
        "max_delivery_attempts",
        "ci_queries",
        "cost_hardcap",
        "duplicate_key",
        "issue",
    ):
        assert data[key] == value["helper"][key]
    with sqlite3.connect(proof) as db:
        assert (
            db.execute("SELECT data FROM delivery_campaigns WHERE name='runner'").fetchone()
            == parent
        )
        after = db.execute("SELECT * FROM delivery_attempts ORDER BY attempt").fetchall()
        assert after[0] == attempts[0]
        assert after[1][2] == attempts[1][2] and after[1][3] == 1
        assert db.execute("SELECT * FROM delivery_events").fetchall()[: len(events)] == events
    assert contents(development) == development_before
    assert not (root / "artifacts/self-improvement/billing-helper/attempt-3").exists()
    # 同じ明示packetを再適用して期限を延長しない。
    before = contents(proof)
    assert delivery.main() == 2
    assert contents(proof) == before


@pytest.mark.parametrize("change", ["head", "merge", "receipt", "http", "approval", "policy"])
def test_creation_entry_rejects_unbound_packet_receipt_or_refusal(creation, change, capsys):
    """preflightだけを厳格化して、実作成入口が未照合のまま進む回帰を防ぐ。"""
    root, proof, development, _ = creation
    if change in {"head", "merge"}:
        packet = root / "artifacts/self-improvement/validation-attempt-3.json"
        data = json.loads(packet.read_text())
        data["corrective_" + change] = "a" * 40
        packet.write_text(json.dumps(data))
    elif change == "receipt":
        receipt = json.loads((root / RECEIPT).read_text())
        receipt["development"]["head"] = "a" * 40
        (root / RECEIPT).write_text(json.dumps(receipt))
    else:
        with sqlite3.connect(proof) as db:
            if change == "http":
                db.execute("UPDATE delivery_http_budget SET used=11")
            else:
                row = db.execute(
                    "SELECT data FROM delivery_campaigns WHERE name='billing-helper'"
                ).fetchone()
                data = json.loads(row[0])
                data["state"] = "approval_wait" if change == "approval" else "failed"
                data["reason"] = (
                    "human_approval_required" if change == "approval" else "policy_denied"
                )
                db.execute(
                    "UPDATE delivery_campaigns SET data=? WHERE name='billing-helper'",
                    (json.dumps(data),),
                )
    # 公開書込み入口のschema初期化は別責務。判定前後のcampaign/attempt/eventを比較する。
    with sqlite3.connect(proof) as db:
        prior = [
            db.execute("SELECT * FROM " + name).fetchall()
            for name in (
                "delivery_campaigns",
                "delivery_attempts",
                "delivery_events",
                "delivery_http_budget",
            )
        ]
    assert delivery.main() == 2
    assert json.loads(capsys.readouterr().out)["state"] == "stopped"
    with sqlite3.connect(proof) as db:
        assert prior == [
            db.execute("SELECT * FROM " + name).fetchall()
            for name in (
                "delivery_campaigns",
                "delivery_attempts",
                "delivery_events",
                "delivery_http_budget",
            )
        ]


@pytest.mark.parametrize("change", [None, "receipt", "ledger", "parent", "base"])
def test_created_attempt_reaches_mocked_child_only_with_unchanged_dependency(
    creation, monkeypatch, capsys, change
):
    """正規作成→helperのbase接続をmock childまで確認。保存後の証拠差替えは拒否する。"""
    root, proof, development, _ = creation
    assert delivery.main() == 0
    capsys.readouterr()
    calls = []
    monkeypatch.setattr(GitHub, "git_transfer", lambda *a: calls.append("mock_fetch"))
    monkeypatch.setattr(helper_job.shutil, "copytree", lambda *a: None)
    monkeypatch.setattr(helper_job, "prepare_runtime", lambda *a: {"fixture": True})
    monkeypatch.setattr(
        helper_job,
        "git",
        lambda workspace, *args: (
            f"https://github.com/{delivery.REPOSITORY}.git"
            if args[0] == "remote"
            else creation[3]["development"]["merge_sha"]
        ),
    )

    def child(task, workspace, prompt, **kwargs):
        assert task.data()["base"] == creation[3]["development"]["merge_sha"]
        assert task.data()["issue"] == 28
        assert task.data()["duplicate_key"] == helper_job.DUPLICATE
        assert task.data()["cli_starts"] == 2
        assert task.data()["http_requests"] == 20
        calls.append("mock_child")
        raise Stop("stopped", "fixture_child_boundary")

    monkeypatch.setattr(helper_job, "run_job", child)
    source = root / "source.json"
    source.write_text(
        json.dumps(
            {
                "debrief_id": "github:yomote/agent-world/pull/24:billing-debrief",
                "finding": "billing_evidence_normalization",
                "source_ref": "sanitized fixture; no Azure read",
                "source_sha256": "a" * 64,
            }
        )
    )
    if change == "receipt":
        with (root / RECEIPT).open("a") as stream:
            stream.write("\n")
    elif change == "ledger":
        with sqlite3.connect(development) as db:
            db.execute("UPDATE delivery_http_budget SET used=10")
    elif change in {"parent", "base"}:
        with sqlite3.connect(proof) as db:
            name = "runner" if change == "parent" else "billing-helper"
            data = json.loads(
                db.execute("SELECT data FROM delivery_campaigns WHERE name=?", (name,)).fetchone()[
                    0
                ]
            )
            data["base"] = "a" * 40
            db.execute(
                "UPDATE delivery_campaigns SET data=? WHERE name=?", (json.dumps(data), name)
            )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "delivery",
            "billing-helper",
            "helper",
            "--source",
            str(source),
        ],
    )
    assert delivery.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == (
        "fixture_child_boundary" if change is None else "development_parent_changed"
    )
    assert calls == (["mock_fetch", "mock_child"] if change is None else [])
