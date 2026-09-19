import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ops_status.api import create_app
from ops_status.models import StatusSnapshot
from ops_status.store import SnapshotConflictError, SnapshotStoreError, VersionedSnapshot


class MemoryStore:
    def __init__(self, value: StatusSnapshot | None = None):
        self.value = value
        self.write_count = 0

    def read(self) -> StatusSnapshot:
        if self.value is None:
            raise FileNotFoundError
        return self.value

    def read_versioned(self) -> VersionedSnapshot:
        return VersionedSnapshot(self.read(), str(self.write_count))

    def write(self, value: StatusSnapshot) -> None:
        self.value = value
        self.write_count += 1

    def write_if_revision(self, value: StatusSnapshot, expected_revision: str | None) -> str:
        actual_revision = None if self.value is None else str(self.write_count)
        if actual_revision != expected_revision:
            raise SnapshotConflictError("changed")
        self.write(value)
        return str(self.write_count)


class ConflictingStore(MemoryStore):
    def write_if_revision(self, value: StatusSnapshot, expected_revision: str | None) -> str:
        raise SnapshotConflictError("changed")


class FailingReadStore(MemoryStore):
    def read_versioned(self) -> VersionedSnapshot:
        raise SnapshotStoreError("unavailable")


def snapshot(received_at: datetime) -> dict:
    return {
        "schema_version": 1,
        "source": "pm-confirmed",
        "observed_at": received_at.isoformat(),
        "received_at": received_at.isoformat(),
        "items": [
            {
                "agent": "management-status-owner",
                "role": "実装担当",
                "task": "Issue #17 管理status最小版",
                "status": "running",
                "observed_at": received_at.isoformat(),
                "issue_url": "https://github.com/yomote/agent-world/issues/17",
            }
        ],
    }


def session_tree(at: datetime) -> dict:
    return {
        "scope": "root session tree",
        "observed_at": at.isoformat(),
        "source": "runtime-list-agents-metadata",
        "root_agent": "management-status-owner",
        "nodes": [{"agent": "management-status-owner", "parent_agent": None}],
        "covered_agents": ["management-status-owner"],
    }


def known_history(at: datetime) -> dict:
    return {
        "recorded_at": at.isoformat(),
        "entries": [
            {
                "agent": "old-worker",
                "parent_agent": "management-status-owner",
                "status": "completed",
                "last_observed_at": None,
                "source": "pm-recorded-completed-work-unit",
            }
        ],
    }


def request_record(request_id: str, at: datetime, *, lifecycle: str = "running") -> dict:
    return {
        "request_id": request_id,
        "scope_id": f"scope-{request_id}",
        "issue_url": f"https://github.com/yomote/agent-world/issues/{request_id.removeprefix('request-')}",
        "issue_state": "closed" if lifecycle == "completed" else "open",
        "issue_observation": "confirmed",
        "issue_observed_at": at.isoformat(),
        "public_title": f"依頼 {request_id}",
        "public_purpose": "公開目的",
        "acceptance_summary": "受入条件",
        "authority_source": "github-issue-observation",
        "lifecycle": lifecycle,
        "owner_agent": "status-owner",
        "member_agents": ["status-owner"],
        "progress_summary": "確認済み進捗",
        "next_action": None if lifecycle == "completed" else "次の作業",
        "dod_source_version": "issue-updated:current",
        "required_requirement_ids": ["domain-use-case"],
        "requirements_contract_digest": "sha256:" + "c" * 64,
        "expected_artifact_head": "a" * 40,
        "report_updated_at": at.isoformat(),
        "report_source": "manual-public-summary",
        "runtime_connection": "record-only",
        "runtime_observed_at": at.isoformat(),
        "evidence": [],
    }


def pm_task_projection(at: datetime) -> dict:
    return {
        "source_kind": "pm-observation",
        "source_version": "issue-45-pm-packet-v1",
        "source_refs": ["https://github.com/yomote/agent-world/issues/45"],
        "observed_at": at.isoformat(),
        "tasks": [
            {
                "task_id": "pr-92-continuity",
                "title": "closure guard",
                "purpose": "未解決taskをownerと次手へ接続する",
                "acceptance_summary": "current mainで再検証する",
                "owner": "governance-continuity",
                "state": "blocked",
                "current_step": "旧base headの受入済み",
                "next_action": "PR 93後にcurrent mainへ統合する",
                "resume_trigger": "PR 93のprotected merge完了",
                "blocker": "merge順待ち",
                "waiting_on": "external",
                "waiting_detail": "PR 93 protected merge",
                "issue_url": "https://github.com/yomote/agent-world/issues/45",
                "pr_url": "https://github.com/yomote/agent-world/pull/92",
                "source_version": "pm-packet-2026-09-19T06:30:00Z",
                "observed_at": at.isoformat(),
                "po_status": "pending",
                "evidence": [],
            }
        ],
    }


def test_pm_task_projection_is_read_only_and_does_not_create_a_front_desk_claim():
    """PM観測cacheをcontrol registryやroot claimへ暗黙昇格しない。"""
    now = datetime.now(UTC)
    data = snapshot(now)
    data["pm_task_projection"] = pm_task_projection(now)
    store = MemoryStore(StatusSnapshot.model_validate(data))
    client = TestClient(create_app(store))

    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["pm_task_projection"]["source_kind"] == "pm-observation"
    assert response.json().get("request_registry") is None
    assert response.json()["active_runtime_bound"] is False

    update = {
        "source": "manual-public-registry",
        "action": "update",
        "expected_generation": 1,
        "actor_front_desk": "invented-front-desk",
        "observed_at": now.isoformat(),
        "requests": [request_record("request-45", now)],
    }
    rejected = client.put("/api/status/requests/upsert", json=update)
    assert rejected.status_code == 409

    later = now + timedelta(seconds=1)
    item = snapshot(later)["items"][0]
    preserved = client.put(
        "/api/status/upsert", json={"source": "local-event-record", "items": [item]}
    )
    assert preserved.status_code == 200
    assert store.value.pm_task_projection is not None
    assert store.value.pm_task_projection.source_version == "issue-45-pm-packet-v1"


def test_pm_task_projection_rejects_older_or_ambiguous_full_replacement():
    """古いPM観測や同一clockの差替えでtaskをfresh化しない。"""
    now = datetime.now(UTC)
    data = snapshot(now)
    data["pm_task_projection"] = pm_task_projection(now)
    store = MemoryStore(StatusSnapshot.model_validate(data))
    changed = snapshot(now + timedelta(seconds=1))
    changed["source"] = "local-event-record"
    changed["pm_task_projection"] = pm_task_projection(now)
    changed["pm_task_projection"]["tasks"][0]["owner"] = "another-owner"

    response = TestClient(create_app(store)).put("/api/status", json=changed)

    assert response.status_code == 409
    assert "PM task cannot change at the same observation" in response.json()["detail"]

    older_row = snapshot(now + timedelta(seconds=2))
    older_row["source"] = "local-event-record"
    older_row["pm_task_projection"] = pm_task_projection(now + timedelta(seconds=2))
    older_row["pm_task_projection"]["tasks"][0]["observed_at"] = (
        now - timedelta(seconds=1)
    ).isoformat()
    older = TestClient(create_app(store)).put("/api/status", json=older_row)
    assert older.status_code == 409
    assert "PM task observation cannot move backwards" in older.json()["detail"]


def test_pm_task_projection_rejects_content_digest_from_other_rows():
    """編集可能なsource URLだけで別内容を同じ版として受理しない。"""
    now = datetime.now(UTC)
    data = snapshot(now)
    data["pm_task_projection"] = pm_task_projection(now)
    data["pm_task_projection"]["content_digest"] = "sha256:" + "0" * 64

    with pytest.raises(ValueError, match="content digest does not match"):
        StatusSnapshot.model_validate(data)


def test_pm_confirmed_upsert_seeds_projection_and_preserves_runtime_state():
    """PM観測の初回保存がruntime行やcontrol registryを置換する回帰を防ぐ。"""
    now = datetime.now(UTC)
    data = snapshot(now)
    original = StatusSnapshot.model_validate(data)
    store = MemoryStore(original)

    response = TestClient(create_app(store)).put(
        "/api/status/upsert",
        json={"source": "pm-confirmed", "pm_task_projection": pm_task_projection(now)},
    )

    assert response.status_code == 200
    assert response.json()["changed"] is True
    assert response.json()["items"] == []
    assert response.json()["pm_task_projection"]["source_kind"] == "pm-observation"
    assert store.value.items == original.items
    assert store.value.request_registry == original.request_registry
    assert store.value.runtime_binding == original.runtime_binding


def test_pm_confirmed_upsert_enforces_task_clock_and_digest_transition():
    """projection全体の新clockだけで古いtaskや同clock差替えをfresh化する回帰を防ぐ。"""
    now = datetime.now(UTC)
    data = snapshot(now)
    data["pm_task_projection"] = pm_task_projection(now)
    store = MemoryStore(StatusSnapshot.model_validate(data))
    changed = pm_task_projection(now + timedelta(seconds=1))
    changed["tasks"][0]["observed_at"] = now.isoformat()
    changed["tasks"][0]["owner"] = "replacement-owner"

    ambiguous = TestClient(create_app(store)).put(
        "/api/status/upsert",
        json={"source": "pm-confirmed", "pm_task_projection": changed},
    )

    assert ambiguous.status_code == 409
    assert "same observation" in ambiguous.json()["detail"]
    assert store.write_count == 0

    older = pm_task_projection(now + timedelta(seconds=2))
    older["tasks"][0]["observed_at"] = (now - timedelta(seconds=1)).isoformat()
    rejected = TestClient(create_app(store)).put(
        "/api/status/upsert",
        json={"source": "pm-confirmed", "pm_task_projection": older},
    )
    assert rejected.status_code == 409
    assert "move backwards" in rejected.json()["detail"]

    globally_newer = snapshot(now + timedelta(seconds=10))
    globally_newer["pm_task_projection"] = pm_task_projection(now + timedelta(seconds=10))
    globally_newer["pm_task_projection"]["tasks"][0]["observed_at"] = now.isoformat()
    global_store = MemoryStore(StatusSnapshot.model_validate(globally_newer))
    older_projection = pm_task_projection(now + timedelta(seconds=5))
    older_projection["tasks"][0]["observed_at"] = now.isoformat()
    global_rejected = TestClient(create_app(global_store)).put(
        "/api/status/upsert",
        json={"source": "pm-confirmed", "pm_task_projection": older_projection},
    )
    assert global_rejected.status_code == 409
    assert "older PM task projection" in global_rejected.json()["detail"]


@pytest.mark.parametrize(
    "mutation", ["missing", "null", "item", "binding", "registry", "runtime-source"]
)
def test_pm_projection_upsert_rejects_missing_or_mixed_write_targets(mutation):
    """PM観測routeからruntime/control更新し、またはruntime routeから投影を変更する回帰を防ぐ。"""
    now = datetime.now(UTC)
    payload = {"source": "pm-confirmed", "pm_task_projection": pm_task_projection(now)}
    if mutation == "missing":
        payload.pop("pm_task_projection")
    elif mutation == "null":
        payload["pm_task_projection"] = None
    elif mutation == "item":
        payload["items"] = snapshot(now)["items"]
    elif mutation == "binding":
        payload["runtime_binding"] = {
            "registry_generation": 1,
            "front_desk_alias": "front",
            "runtime_session_digest": "sha256:" + "a" * 64,
        }
    elif mutation == "registry":
        payload["request_registry"] = {}
    else:
        payload["source"] = "local-event-record"
    response = TestClient(
        create_app(MemoryStore(StatusSnapshot.model_validate(snapshot(now))))
    ).put("/api/status/upsert", json=payload)
    assert response.status_code == 422
def accepted_closure_audit(request_id: str, at: datetime) -> dict:
    gate = {
        "state": "achieved",
        "evidence_refs": ["https://github.com/yomote/agent-world/pull/91"],
        "reason": None,
    }
    return {
        "schema_version": 1,
        "request_id": request_id,
        "objective_ref": (
            f"https://github.com/yomote/agent-world/issues/{request_id.removeprefix('request-')}"
        ),
        "dod_source_version": "issue-updated:current",
        "requirements_contract_digest": "sha256:" + "c" * 64,
        "evidence_head": "a" * 40,
        "checker_agent": "independent-checker",
        "checked_at": at.isoformat(),
        **{
            name: gate.copy()
            for name in ("code_done", "verified", "reviewed", "merged", "delivered", "purpose")
        },
        "requirements": [
            {
                "requirement_id": "domain-use-case",
                "category": "domain",
                "source_version": "issue-updated:current",
                "verification_method": "use-case acceptance",
                "evidence_refs": ["https://github.com/yomote/agent-world/issues/45"],
                "evidence_head": "a" * 40,
                "checker_agent": "independent-checker",
                "result": "satisfied",
                "reason": None,
            }
        ],
        "overall": "accept",
        "owner": None,
        "next_action": None,
        "resume_trigger": None,
        "stop_decision": {"made": False, "reason": None},
    }


def test_request_registry_completed_write_requires_matching_closure_audit():
    """PMのdone自己申告だけでcompletedを保存する回帰を防ぐ。"""
    now = datetime.now(UTC)
    store = MemoryStore(StatusSnapshot.model_validate(snapshot(now)))
    client = TestClient(create_app(store))
    running = request_record("request-45", now, lifecycle="running")
    payload = {
        "source": "manual-public-registry",
        "action": "initialize",
        "expected_generation": 0,
        "actor_front_desk": "front-desk-1",
        "observed_at": now.isoformat(),
        "requests": [running],
    }
    initialized = client.put("/api/status/requests/upsert", json=payload)
    assert initialized.status_code == 200

    completed_at = now + timedelta(seconds=1)
    record = request_record("request-45", completed_at, lifecycle="completed")
    update = {
        **payload,
        "action": "update",
        "expected_generation": 1,
        "observed_at": completed_at.isoformat(),
        "requests": [record],
    }

    rejected = client.put("/api/status/requests/upsert", json=update)

    assert rejected.status_code == 422
    assert store.write_count == 1

    record["closure_audit"] = accepted_closure_audit("request-45", completed_at)
    record["po_review_required"] = True
    pending_po = client.put("/api/status/requests/upsert", json=update)
    assert pending_po.status_code == 422
    record["po_acceptance_receipt"] = {
        "schema_version": 1,
        "dedup_key": "sha256:" + "b" * 64,
        "request_id": "request-45",
        "evidence_head": "a" * 40,
        "dod_source_version": "issue-updated:current",
        "requirements_contract_digest": "sha256:" + "c" * 64,
        "decision": "accepted",
        "acknowledged_at": completed_at.isoformat(),
        "channel": "front-desk-same-thread",
        "message_ref": "pm-message-1",
    }
    accepted = client.put("/api/status/requests/upsert", json=update)

    assert accepted.status_code == 200
    assert store.value.request_registry.requests[0].lifecycle == "completed"


def test_legacy_completed_snapshot_remains_readable_without_closure_audit():
    """導入前のcompleted記録を新しい保存guardで読めなくする回帰を防ぐ。"""
    now = datetime.now(UTC)
    data = snapshot(now)
    data["request_registry"] = {
        "generation": 1,
        "active_front_desk": {"alias": "front-desk-1", "claimed_at": now.isoformat()},
        "updated_at": now.isoformat(),
        "source": "manual-public-registry",
        "requests": [request_record("request-45", now, lifecycle="completed")],
    }
    legacy = data["request_registry"]["requests"][0]
    for field in (
        "dod_source_version",
        "required_requirement_ids",
        "requirements_contract_digest",
        "expected_artifact_head",
    ):
        legacy.pop(field)

    parsed = StatusSnapshot.model_validate(data)

    assert parsed.request_registry.requests[0].closure_audit is None


def test_request_registry_rejects_direct_completed_initialization():
    """事前保存したAC契約を経ずhandcrafted auditで完了を初期化する回帰を防ぐ。"""
    now = datetime.now(UTC)
    store = MemoryStore(StatusSnapshot.model_validate(snapshot(now)))
    record = request_record("request-45", now, lifecycle="completed")
    record["closure_audit"] = accepted_closure_audit("request-45", now)

    response = TestClient(create_app(store)).put(
        "/api/status/requests/upsert",
        json={
            "source": "manual-public-registry",
            "action": "initialize",
            "expected_generation": 0,
            "actor_front_desk": "front-desk-1",
            "observed_at": now.isoformat(),
            "requests": [record],
        },
    )

    assert response.status_code == 409
    assert store.write_count == 0

    running = request_record("request-44", now, lifecycle="running")
    initialized = TestClient(create_app(store)).put(
        "/api/status/requests/upsert",
        json={
            "source": "manual-public-registry",
            "action": "initialize",
            "expected_generation": 0,
            "actor_front_desk": "front-desk-1",
            "observed_at": now.isoformat(),
            "requests": [running],
        },
    )
    assert initialized.status_code == 200
    added_at = now + timedelta(seconds=1)
    added = request_record("request-45", added_at, lifecycle="completed")
    added["closure_audit"] = accepted_closure_audit("request-45", added_at)
    added_response = TestClient(create_app(store)).put(
        "/api/status/requests/upsert",
        json={
            "source": "manual-public-registry",
            "action": "update",
            "expected_generation": 1,
            "actor_front_desk": "front-desk-1",
            "observed_at": added_at.isoformat(),
            "requests": [added],
        },
    )
    assert added_response.status_code == 409


def test_request_registry_completion_cannot_shrink_saved_contract():
    """完了時だけ必須ACを減らし自己申告acceptへ差し替える回帰を防ぐ。"""
    now = datetime.now(UTC)
    store = MemoryStore(StatusSnapshot.model_validate(snapshot(now)))
    client = TestClient(create_app(store))
    running = request_record("request-45", now, lifecycle="running")
    initialized = client.put(
        "/api/status/requests/upsert",
        json={
            "source": "manual-public-registry",
            "action": "initialize",
            "expected_generation": 0,
            "actor_front_desk": "front-desk-1",
            "observed_at": now.isoformat(),
            "requests": [running],
        },
    )
    assert initialized.status_code == 200

    completed_at = now + timedelta(seconds=1)
    completed = request_record("request-45", completed_at, lifecycle="completed")
    completed["required_requirement_ids"] = ["reduced-contract"]
    audit = accepted_closure_audit("request-45", completed_at)
    audit["requirements"][0]["requirement_id"] = "reduced-contract"
    completed["closure_audit"] = audit
    response = client.put(
        "/api/status/requests/upsert",
        json={
            "source": "manual-public-registry",
            "action": "update",
            "expected_generation": 1,
            "actor_front_desk": "front-desk-1",
            "observed_at": completed_at.isoformat(),
            "requests": [completed],
        },
    )

    assert response.status_code == 409
    assert "previously saved requirements contract" in response.json()["detail"]


def test_request_registry_report_clock_covers_closure_contract_fields():
    """同じreport clockで契約・closure証跡だけを差し替える回帰を防ぐ。"""
    now = datetime.now(UTC)
    store = MemoryStore(StatusSnapshot.model_validate(snapshot(now)))
    client = TestClient(create_app(store))
    running = request_record("request-45", now, lifecycle="running")
    initialized = client.put(
        "/api/status/requests/upsert",
        json={
            "source": "manual-public-registry",
            "action": "initialize",
            "expected_generation": 0,
            "actor_front_desk": "front-desk-1",
            "observed_at": now.isoformat(),
            "requests": [running],
        },
    )
    assert initialized.status_code == 200

    replacement = dict(running)
    replacement["requirements_contract_digest"] = "sha256:" + "d" * 64
    response = client.put(
        "/api/status/requests/upsert",
        json={
            "source": "manual-public-registry",
            "action": "update",
            "expected_generation": 1,
            "actor_front_desk": "front-desk-1",
            "observed_at": (now + timedelta(seconds=1)).isoformat(),
            "requests": [replacement],
        },
    )

    assert response.status_code == 409
    assert "request report" in response.json()["detail"]


def test_status_marks_old_received_snapshot_stale(tmp_path, monkeypatch):
    """更新が止まったsnapshotを現在稼働中に見せ続ける回帰を防ぐ。"""
    old = datetime.now(UTC) - timedelta(minutes=3)
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot(old)), encoding="utf-8")
    monkeypatch.setenv("AGENT_WORLD_STATUS_SNAPSHOT", str(path))

    response = TestClient(create_app()).get("/api/status")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["stale"] is True
    assert response.json()["age_seconds"] >= 180


def test_status_rejects_unknown_source(tmp_path, monkeypatch):
    """fixtureや手動snapshotをlive eventと誤表示する未知sourceを拒否する。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["source"] = "live-ish"
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("AGENT_WORLD_STATUS_SNAPSHOT", str(path))

    response = TestClient(create_app()).get("/api/status")

    assert response.status_code == 503
    assert response.json()["detail"] == "status snapshot is invalid"


def test_status_page_is_served_from_same_origin():
    response = TestClient(create_app()).get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "connect-src 'self'" in response.text
    assert "api.github.com" not in response.text


@pytest.mark.parametrize("path", ["/status.css", "/status.js"])
def test_status_assets_do_not_keep_an_old_dashboard_after_deploy(path):
    """公開更新後も端末cacheが旧dashboardを表示し続ける回帰を防ぐ。"""
    response = TestClient(create_app()).get(path)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_request_registry_initializes_and_returns_only_submitted_requests():
    """部分更新receiptが保持済み依頼をGETするoracleになる回帰を防ぐ。"""
    now = datetime.now(UTC)
    store = MemoryStore(StatusSnapshot.model_validate(snapshot(now)))
    client = TestClient(create_app(store))
    payload = {
        "source": "manual-public-registry",
        "action": "initialize",
        "expected_generation": 0,
        "actor_front_desk": "front-desk-1",
        "observed_at": now.isoformat(),
        "requests": [request_record("request-59", now), request_record("request-64", now)],
    }

    response = client.put("/api/status/requests/upsert", json=payload)

    assert response.status_code == 200
    assert response.json()["generation"] == 1
    assert [item["request_id"] for item in response.json()["requests"]] == [
        "request-59",
        "request-64",
    ]
    assert store.value.request_registry.active_front_desk.alias == "front-desk-1"


def test_request_registry_update_preserves_other_requests_and_rejects_stale_generation():
    """指定外依頼の消去と、古いFront Deskによるlost updateを防ぐ。"""
    now = datetime.now(UTC)
    store = MemoryStore(StatusSnapshot.model_validate(snapshot(now)))
    client = TestClient(create_app(store))
    base = {
        "source": "manual-public-registry",
        "action": "initialize",
        "expected_generation": 0,
        "actor_front_desk": "front-desk-1",
        "observed_at": now.isoformat(),
        "requests": [request_record("request-59", now), request_record("request-64", now)],
    }
    assert client.put("/api/status/requests/upsert", json=base).status_code == 200
    later = now + timedelta(minutes=1)
    changed = request_record("request-64", later)
    changed["progress_summary"] = "公開済み"
    update = {
        **base,
        "action": "update",
        "expected_generation": 1,
        "observed_at": later.isoformat(),
        "requests": [changed],
    }

    response = client.put("/api/status/requests/upsert", json=update)

    assert response.status_code == 200
    assert response.json()["generation"] == 2
    assert "active_front_desk" not in response.json()
    assert "handover" not in response.json()
    assert [item["request_id"] for item in response.json()["requests"]] == ["request-64"]
    assert {item.request_id for item in store.value.request_registry.requests} == {
        "request-59",
        "request-64",
    }
    assert client.put("/api/status/requests/upsert", json=update).status_code == 409


def test_request_registry_rejects_independent_clock_conflicts_and_keeps_noop_generation():
    """新しい受信時刻で古いIssue/runtime内容を上書きする回帰を防ぐ。"""
    now = datetime.now(UTC)
    store = MemoryStore(StatusSnapshot.model_validate(snapshot(now)))
    client = TestClient(create_app(store))
    record = request_record("request-64", now)
    initialize = {
        "source": "manual-public-registry",
        "action": "initialize",
        "expected_generation": 0,
        "actor_front_desk": "front-desk-1",
        "observed_at": now.isoformat(),
        "requests": [record],
    }
    client.put("/api/status/requests/upsert", json=initialize)
    noop = {**initialize, "action": "update", "expected_generation": 1}
    response = client.put("/api/status/requests/upsert", json=noop)
    assert response.status_code == 200
    assert response.json()["changed"] is False
    assert response.json()["generation"] == 1

    conflict = request_record("request-64", now + timedelta(minutes=1))
    conflict["public_title"] = "同じIssue時計の異なるcache"
    conflict["issue_observed_at"] = now.isoformat()
    response = client.put(
        "/api/status/requests/upsert",
        json={
            **noop,
            "observed_at": (now + timedelta(minutes=1)).isoformat(),
            "requests": [conflict],
        },
    )
    assert response.status_code == 409
    clock_regression = record.copy()
    clock_regression["report_updated_at"] = (now - timedelta(seconds=1)).isoformat()
    response = client.put(
        "/api/status/requests/upsert",
        json={
            **noop,
            "observed_at": (now + timedelta(minutes=2)).isoformat(),
            "requests": [clock_regression],
        },
    )
    assert response.status_code == 409


def test_handover_requires_exact_bundle_and_does_not_restart_recorded_workers():
    """二重claim、別successor、暗黙worker再開を防ぐ。"""
    now = datetime.now(UTC)
    data = snapshot(now)
    data["runtime_capacity"] = {
        "scope": "old root",
        "observed_at": now.isoformat(),
        "state_source": "runtime-list-agents-metadata",
        "running": 1,
    }
    data["focus_summary"] = {
        "purpose": "旧root",
        "progress_summary": "旧進捗",
        "next_action": "旧次手",
        "updated_at": now.isoformat(),
        "source": "manual-public-summary",
    }
    data["session_tree"] = session_tree(now)
    data["known_history"] = known_history(now)
    data["runtime_binding"] = {
        "registry_generation": 1,
        "front_desk_alias": "front-desk-1",
        "runtime_session_digest": "sha256:" + "c" * 64,
    }
    store = MemoryStore(StatusSnapshot.model_validate(data))
    client = TestClient(create_app(store))
    record = request_record("request-64", now)
    initialize = {
        "source": "manual-public-registry",
        "action": "initialize",
        "expected_generation": 0,
        "actor_front_desk": "front-desk-1",
        "observed_at": now.isoformat(),
        "requests": [record],
    }
    client.put("/api/status/requests/upsert", json=initialize)
    digest = "sha256:" + "a" * 64
    prepared_at = now + timedelta(minutes=1)
    prepare = {
        "source": "manual-public-registry",
        "action": "prepare-handover",
        "expected_generation": 1,
        "actor_front_desk": "front-desk-1",
        "observed_at": prepared_at.isoformat(),
        "successor_front_desk": "front-desk-2",
        "bundle_digest": digest,
    }
    assert (
        client.put(
            "/api/status/requests/upsert", json={**prepare, "requests": [record]}
        ).status_code
        == 422
    )
    assert client.put("/api/status/requests/upsert", json=prepare).json()["generation"] == 2
    claim = {
        **prepare,
        "action": "claim-handover",
        "expected_generation": 2,
        "actor_front_desk": "front-desk-2",
        "actor_runtime_session_id": "runtime-session-new",
        "observed_at": (prepared_at + timedelta(minutes=1)).isoformat(),
    }

    missing_runtime = claim.copy()
    missing_runtime.pop("actor_runtime_session_id")
    assert client.put("/api/status/requests/upsert", json=missing_runtime).status_code == 422

    response = client.put("/api/status/requests/upsert", json=claim)

    assert response.status_code == 200
    assert response.json()["active_front_desk"]["alias"] == "front-desk-2"
    assert response.json()["active_front_desk"]["runtime_session_id"] == "runtime-session-new"
    stored = store.value.request_registry.requests[0]
    assert stored.runtime_connection == "record-only"
    assert stored.lifecycle == "handover-waiting"
    assert store.value.items == []
    assert store.value.runtime_capacity is None
    assert store.value.focus_summary is None
    assert store.value.session_tree is None
    assert store.value.known_history == StatusSnapshot.model_validate(data).known_history
    assert store.value.runtime_binding is None
    assert client.put("/api/status/requests/upsert", json=claim).status_code == 409


def test_status_after_claim_requires_the_active_root_runtime_binding():
    """旧publisherやchildがclaim後のcurrent runtime表示を復活させる回帰を防ぐ。"""
    now = datetime.now(UTC)
    store = MemoryStore(StatusSnapshot.model_validate(snapshot(now)))
    client = TestClient(create_app(store))
    record = request_record("request-82", now)
    initialize = {
        "source": "manual-public-registry",
        "action": "initialize",
        "expected_generation": 0,
        "actor_front_desk": "front-desk-1",
        "observed_at": now.isoformat(),
        "requests": [record],
    }
    assert client.put("/api/status/requests/upsert", json=initialize).status_code == 200
    digest = "sha256:" + "b" * 64
    prepared_at = now + timedelta(minutes=1)
    prepare = {
        **initialize,
        "action": "prepare-handover",
        "expected_generation": 1,
        "observed_at": prepared_at.isoformat(),
        "requests": [],
        "successor_front_desk": "front-desk-2",
        "bundle_digest": digest,
    }
    assert client.put("/api/status/requests/upsert", json=prepare).status_code == 200
    claim = {
        **prepare,
        "action": "claim-handover",
        "expected_generation": 2,
        "actor_front_desk": "front-desk-2",
        "actor_runtime_session_id": "runtime-root-new",
        "observed_at": (prepared_at + timedelta(minutes=1)).isoformat(),
    }
    assert client.put("/api/status/requests/upsert", json=claim).status_code == 200
    item = snapshot(prepared_at + timedelta(minutes=2))["items"][0]

    missing = client.put(
        "/api/status/upsert", json={"source": "local-event-record", "items": [item]}
    )
    wrong = client.put(
        "/api/status/upsert",
        json={
            "source": "local-event-record",
            "items": [item],
            "runtime_binding": {
                "registry_generation": 3,
                "front_desk_alias": "front-desk-2",
                "runtime_session_digest": "sha256:" + hashlib.sha256(b"runtime-child").hexdigest(),
            },
        },
    )
    assert missing.status_code == 409
    assert wrong.status_code == 409
    assert store.value.items == []

    binding = {
        "registry_generation": 3,
        "front_desk_alias": "front-desk-2",
        "runtime_session_digest": "sha256:" + hashlib.sha256(b"runtime-root-new").hexdigest(),
    }
    accepted = client.put(
        "/api/status/upsert",
        json={"source": "local-event-record", "items": [item], "runtime_binding": binding},
    )

    assert accepted.status_code == 200
    assert accepted.json()["runtime_binding"] == binding
    assert store.value.runtime_binding is not None
    assert store.value.runtime_binding.runtime_session_digest == binding["runtime_session_digest"]

    public = client.get("/api/status")
    assert public.status_code == 200
    assert public.json()["active_runtime_bound"] is True
    assert public.json()["runtime_binding_verified"] is True
    assert "runtime_session_id" not in public.json()["request_registry"]["active_front_desk"]
    assert "runtime_binding" not in public.json()

    changed_request = store.value.request_registry.requests[0].model_dump(mode="json")
    changed_request["progress_summary"] = "claim後の通常更新"
    changed_request["report_updated_at"] = (prepared_at + timedelta(minutes=3)).isoformat()
    registry_update = {
        "source": "manual-public-registry",
        "action": "update",
        "expected_generation": 3,
        "actor_front_desk": "front-desk-2",
        "observed_at": (prepared_at + timedelta(minutes=3)).isoformat(),
        "requests": [changed_request],
    }
    updated_registry = client.put("/api/status/requests/upsert", json=registry_update)
    assert updated_registry.status_code == 200
    assert updated_registry.json()["generation"] == 4

    later_item = snapshot(prepared_at + timedelta(minutes=4))["items"][0]
    still_accepted = client.put(
        "/api/status/upsert",
        json={
            "source": "local-event-record",
            "items": [later_item],
            "runtime_binding": binding,
        },
    )
    assert still_accepted.status_code == 200
    assert store.value.request_registry.active_front_desk.claim_generation == 3


def test_full_put_cannot_reinject_status_without_the_claimed_root_binding():
    """full PUTでbinding検証を迂回して旧root表示を戻す回帰を防ぐ。"""
    now = datetime.now(UTC)
    store = MemoryStore(StatusSnapshot.model_validate(snapshot(now)))
    client = TestClient(create_app(store))
    initialize = {
        "source": "manual-public-registry",
        "action": "initialize",
        "expected_generation": 0,
        "actor_front_desk": "front-desk-1",
        "observed_at": now.isoformat(),
        "requests": [request_record("request-82", now)],
    }
    assert client.put("/api/status/requests/upsert", json=initialize).status_code == 200
    prepared_at = now + timedelta(minutes=1)
    prepare = {
        **initialize,
        "action": "prepare-handover",
        "expected_generation": 1,
        "observed_at": prepared_at.isoformat(),
        "requests": [],
        "successor_front_desk": "front-desk-2",
        "bundle_digest": "sha256:" + "d" * 64,
    }
    assert client.put("/api/status/requests/upsert", json=prepare).status_code == 200
    claim = {
        **prepare,
        "action": "claim-handover",
        "expected_generation": 2,
        "actor_front_desk": "front-desk-2",
        "actor_runtime_session_id": "runtime-root-new",
        "observed_at": (prepared_at + timedelta(minutes=1)).isoformat(),
    }
    assert client.put("/api/status/requests/upsert", json=claim).status_code == 200

    incoming = store.value.model_dump(mode="json")
    incoming["source"] = "local-event-record"
    incoming["observed_at"] = (prepared_at + timedelta(minutes=2)).isoformat()
    incoming["received_at"] = (prepared_at + timedelta(minutes=2)).isoformat()
    incoming["items"] = snapshot(prepared_at + timedelta(minutes=2))["items"]

    rejected = client.put("/api/status", json=incoming)
    assert rejected.status_code == 409
    assert store.value.items == []

    incoming["runtime_binding"] = {
        "registry_generation": 3,
        "front_desk_alias": "front-desk-2",
        "runtime_session_digest": "sha256:" + hashlib.sha256(b"runtime-root-new").hexdigest(),
    }
    accepted = client.put("/api/status", json=incoming)
    assert accepted.status_code == 204
    assert store.value.items


def test_legacy_snapshot_with_unverified_runtime_binding_remains_readable():
    """保存済みv1を503にせず、current runtimeだけをfail-closedにする。"""
    now = datetime.now(UTC)
    data = snapshot(now)
    data["request_registry"] = {
        "generation": 1,
        "active_front_desk": {
            "alias": "front-desk-1",
            "claimed_at": now.isoformat(),
            "runtime_session_id": "legacy-runtime",
            "runtime_observed_at": now.isoformat(),
        },
        "updated_at": now.isoformat(),
        "source": "manual-public-registry",
        "requests": [request_record("request-legacy", now)],
        "handover": {
            "state": "accepted",
            "from_front_desk": "front-desk-old",
            "to_front_desk": "front-desk-1",
            "bundle_digest": "sha256:" + "e" * 64,
            "prepared_at": (now - timedelta(minutes=1)).isoformat(),
            "accepted_at": now.isoformat(),
            "resume_policy": "explicit-dispatch-required",
        },
    }
    store = MemoryStore(StatusSnapshot.model_validate(data))
    client = TestClient(create_app(store))
    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["active_runtime_bound"] is True
    assert response.json()["runtime_binding_verified"] is False
    assert "runtime_session_id" not in response.json()["request_registry"]["active_front_desk"]

    binding = {
        "registry_generation": 1,
        "front_desk_alias": "front-desk-1",
        "runtime_session_digest": "sha256:" + hashlib.sha256(b"legacy-runtime").hexdigest(),
    }
    item = snapshot(now + timedelta(minutes=1))["items"][0]
    accepted = client.put(
        "/api/status/upsert",
        json={"source": "local-event-record", "items": [item], "runtime_binding": binding},
    )
    assert accepted.status_code == 200
    assert client.get("/api/status").json()["runtime_binding_verified"] is True


def test_full_and_status_upsert_preserve_request_registry():
    """既存publisherがregistryを暗黙消去する回帰を防ぐ。"""
    now = datetime.now(UTC)
    store = MemoryStore(StatusSnapshot.model_validate(snapshot(now)))
    client = TestClient(create_app(store))
    initialize = {
        "source": "manual-public-registry",
        "action": "initialize",
        "expected_generation": 0,
        "actor_front_desk": "front-desk-1",
        "observed_at": now.isoformat(),
        "requests": [request_record("request-64", now)],
    }
    client.put("/api/status/requests/upsert", json=initialize)
    full = snapshot(now + timedelta(minutes=1))
    full["source"] = "local-event-record"
    assert client.put("/api/status", json=full).status_code == 409
    partial_item = snapshot(now + timedelta(minutes=2))["items"][0]
    partial = {"source": "local-event-record", "items": [partial_item]}
    assert client.put("/api/status/upsert", json=partial).status_code == 200
    assert store.value.request_registry.requests[0].request_id == "request-64"


@pytest.mark.parametrize(
    "store",
    [MemoryStore(), MemoryStore(StatusSnapshot.model_validate(snapshot(datetime.now(UTC))))],
)
def test_full_put_cannot_initialize_request_registry(store):
    """full publisherが専用registry CASとactive owner規則を迂回する回帰を防ぐ。"""
    fixture = Path(__file__).parents[3] / "scripts/tests/fixtures/request_registry_status.json"
    incoming = json.loads(fixture.read_text(encoding="utf-8"))
    incoming["source"] = "local-event-record"

    response = TestClient(create_app(store)).put("/api/status", json=incoming)

    assert response.status_code == 409


@pytest.mark.parametrize("expected_upper", [False, True])
def test_azure_auth_separates_operator_reads_from_ingest_writes(monkeypatch, expected_upper):
    """同じOIDのcase差による拒否と、本人/ingestの権限逆転を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["source"] = "local-event-record"
    store = MemoryStore(StatusSnapshot.model_validate(data))
    client = TestClient(create_app(store))
    monkeypatch.setenv("AGENT_WORLD_STATUS_REQUIRE_AUTH", "true")
    operator = "11111111-1111-4111-8111-aaaaaaaaaaaa"
    ingest = "22222222-2222-4222-8222-bbbbbbbbbbbb"
    expected_operator = operator.upper() if expected_upper else operator
    expected_ingest = ingest.upper() if expected_upper else ingest
    actual_operator = operator if expected_upper else operator.upper()
    actual_ingest = ingest if expected_upper else ingest.upper()
    monkeypatch.setenv("AGENT_WORLD_STATUS_OPERATOR_OBJECT_ID", expected_operator)
    monkeypatch.setenv("AGENT_WORLD_STATUS_INGEST_OBJECT_ID", expected_ingest)

    assert client.get("/", headers={"x-ms-client-principal-id": actual_ingest}).status_code == 403
    assert client.get("/", headers={"x-ms-client-principal-id": actual_operator}).status_code == 200
    assert (
        client.get("/api/status", headers={"x-ms-client-principal-id": actual_operator}).status_code
        == 200
    )
    assert (
        client.put(
            "/api/status",
            headers={"x-ms-client-principal-id": actual_operator},
            json=data,
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/api/status",
            headers={"x-ms-client-principal-id": actual_ingest},
            json=data,
        ).status_code
        == 204
    )
    assert (
        client.put(
            "/api/status/upsert",
            headers={"x-ms-client-principal-id": actual_ingest},
            json={"source": "local-event-record", "items": data["items"]},
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/api/status/upsert",
            headers={"x-ms-client-principal-id": actual_operator},
            json={"source": "local-event-record", "items": data["items"]},
        ).status_code
        == 403
    )
    projection_update = {
        "source": "pm-confirmed",
        "pm_task_projection": pm_task_projection(current),
    }
    assert (
        client.put(
            "/api/status/upsert",
            headers={"x-ms-client-principal-id": actual_operator},
            json=projection_update,
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/api/status/upsert",
            headers={"x-ms-client-principal-id": actual_ingest},
            json=projection_update,
        ).status_code
        == 200
    )
    registry_update = {
        "source": "manual-public-registry",
        "action": "initialize",
        "expected_generation": 0,
        "actor_front_desk": "front-desk-1",
        "observed_at": current.isoformat(),
        "requests": [request_record("request-64", current)],
    }
    assert (
        client.put(
            "/api/status/requests/upsert",
            headers={"x-ms-client-principal-id": actual_operator},
            json=registry_update,
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/api/status/requests/upsert",
            headers={"x-ms-client-principal-id": actual_ingest},
            json=registry_update,
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "expected,actual",
    [("", ""), ("invalid", "invalid"), ("11111111-1111-4111-8111-aaaaaaaaaaaa", "invalid")],
)
def test_azure_auth_rejects_invalid_principal_ids(monkeypatch, expected, actual):
    """GUID正規化で空欄や一致する不正OIDまで認可する回帰を防ぐ。"""
    monkeypatch.setenv("AGENT_WORLD_STATUS_REQUIRE_AUTH", "true")
    monkeypatch.setenv("AGENT_WORLD_STATUS_OPERATOR_OBJECT_ID", expected)
    client = TestClient(create_app(MemoryStore()))

    assert client.get("/", headers={"x-ms-client-principal-id": actual}).status_code == 403


def test_ingest_does_not_rewrite_blob_for_same_observation():
    """同じeventの再送で保存時刻やBlobを更新し続ける回帰を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["source"] = "local-event-record"
    store = MemoryStore(StatusSnapshot.model_validate(data))

    response = TestClient(create_app(store)).put("/api/status", json=data)

    assert response.status_code == 204
    assert store.write_count == 0


def test_ingest_keeps_state_transition_for_same_source_observation():
    """同じtask event時刻でactivityがstaleへ変わったsnapshotを落とす回帰を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["source"] = "local-event-record"
    store = MemoryStore(StatusSnapshot.model_validate(data))
    changed = snapshot(current)
    changed["source"] = "local-event-record"
    changed["received_at"] = (current + timedelta(seconds=1)).isoformat()
    changed["items"][0].update(
        {
            "status": "unknown",
            "latest_activity": "structured-item",
            "latest_activity_at": current.isoformat(),
            "stale": True,
        }
    )

    response = TestClient(create_app(store)).put("/api/status", json=changed)

    assert response.status_code == 204
    assert store.write_count == 1
    assert store.value.items[0].status == "unknown"
    assert store.value.items[0].stale is True


def test_ingest_rejects_manual_snapshot():
    """手動snapshotを外部runtime eventとして公開する回帰を防ぐ。"""
    response = TestClient(create_app(MemoryStore())).put(
        "/api/status", json=snapshot(datetime.now(UTC))
    )

    assert response.status_code == 422


def test_ingest_upsert_preserves_other_rows_and_omitted_capacity():
    """部分更新が未指定rowやcapacityを全snapshot置換で消す回帰を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["runtime_capacity"] = {
        "scope": "root session tree",
        "observed_at": current.isoformat(),
        "state_source": "runtime-list-agents-metadata",
        "running": 2,
        "total": 2,
    }
    data["items"].append(
        {
            "agent": "preserved-owner",
            "role": "運用担当",
            "task": "保持対象",
            "status": "running",
            "observed_at": (current - timedelta(days=1)).isoformat(),
            "note": "ingestへ返してはいけない既存情報",
        }
    )
    store = MemoryStore(StatusSnapshot.model_validate(data))
    updated = snapshot(current)["items"][0]
    updated["current_action"] = "部分更新を検証"
    updated["summary_updated_at"] = current.isoformat()

    response = TestClient(create_app(store)).put(
        "/api/status/upsert",
        json={"source": "local-event-record", "items": [updated]},
    )

    assert response.status_code == 200
    assert response.json()["changed"] is True
    assert response.json()["revision"] == "1"
    assert [item["agent"] for item in response.json()["items"]] == ["management-status-owner"]
    assert "runtime_capacity" not in response.json()
    assert "preserved-owner" not in response.text
    assert store.value.source == "ingest-upsert"
    assert [item.agent for item in store.value.items] == [
        "management-status-owner",
        "preserved-owner",
    ]
    assert store.value.items[1].note == "ingestへ返してはいけない既存情報"
    assert store.value.items[1].observed_at == current - timedelta(days=1)
    assert store.value.received_at > store.value.items[1].observed_at
    assert store.value.runtime_capacity == StatusSnapshot.model_validate(data).runtime_capacity


def test_ingest_upsert_replaces_capacity_only_when_supplied():
    """capacity指定時だけ保存値と安全なreceiptを更新する。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    store = MemoryStore(StatusSnapshot.model_validate(data))
    capacity = {
        "scope": "root session tree",
        "observed_at": current.isoformat(),
        "state_source": "runtime-list-agents-metadata",
        "limit_source": "runtime-instructions",
        "running": 4,
        "idle": 0,
        "completed": 0,
        "total": 4,
        "max_concurrent_agents": 8,
    }

    response = TestClient(create_app(store)).put(
        "/api/status/upsert",
        json={"source": "local-event-record", "runtime_capacity": capacity},
    )

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["runtime_capacity"]["running"] == 4
    assert store.value.runtime_capacity.running == 4


def test_ingest_upsert_preserves_and_receipts_explicit_focus_and_relationships():
    """明示された今回要約と委任関係だけを保存し、他行をreceiptへ漏らさない。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["items"].append(
        {
            "agent": "preserved",
            "role": "既存担当",
            "task": "保持対象",
            "status": "completed",
            "observed_at": current.isoformat(),
        }
    )
    item = data["items"][0].copy()
    item.update(
        {
            "parent_relation": "root",
            "parent_source": "runtime-canonical-task-path",
            "parent_observed_at": current.isoformat(),
            "instruction_summary": "公開用の指示要約",
            "current_action": "関係表示を実装",
            "summary_updated_at": current.isoformat(),
        }
    )
    focus = {
        "purpose": "誰が何をしているか把握する",
        "progress_summary": "schemaを実装",
        "blocker": "公開待ち",
        "next_action": "画面を検証",
        "updated_at": current.isoformat(),
        "source": "manual-public-summary",
    }
    store = MemoryStore(StatusSnapshot.model_validate(data))

    response = TestClient(create_app(store)).put(
        "/api/status/upsert",
        json={"source": "local-event-record", "items": [item], "focus_summary": focus},
    )

    assert response.status_code == 200
    saved_focus = response.json()["focus_summary"]
    assert saved_focus["purpose"] == focus["purpose"]
    assert saved_focus["progress_summary"] == focus["progress_summary"]
    assert datetime.fromisoformat(saved_focus["updated_at"].replace("Z", "+00:00")) == current
    assert [row["agent"] for row in response.json()["items"]] == ["management-status-owner"]
    assert "preserved" not in response.text
    assert [row.agent for row in store.value.items] == ["management-status-owner", "preserved"]
    assert store.value.items[0].instruction_summary == "公開用の指示要約"

    next_item = item.copy()
    next_item["observed_at"] = (current + timedelta(seconds=1)).isoformat()
    next_item["current_action"] = "次の作業"
    next_item["summary_updated_at"] = (current + timedelta(seconds=1)).isoformat()
    preserved = TestClient(create_app(store)).put(
        "/api/status/upsert",
        json={"source": "local-event-record", "items": [next_item]},
    )
    assert preserved.status_code == 200
    assert "focus_summary" not in preserved.json()
    assert store.value.focus_summary is not None
    assert store.value.focus_summary.purpose == focus["purpose"]


def test_ingest_upsert_cannot_clear_parent_relation_with_newer_item_observation():
    """新しい状態観測だけで確認済みの親関係を未取得へ戻す回帰を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["items"][0].update(
        {
            "parent_relation": "root",
            "parent_source": "runtime-canonical-task-path",
            "parent_observed_at": current.isoformat(),
        }
    )
    incoming = data["items"][0].copy()
    incoming["observed_at"] = (current + timedelta(seconds=1)).isoformat()
    for field in ("parent_relation", "parent_source", "parent_observed_at"):
        incoming.pop(field)
    store = MemoryStore(StatusSnapshot.model_validate(data))

    response = TestClient(create_app(store)).put(
        "/api/status/upsert", json={"source": "local-event-record", "items": [incoming]}
    )

    assert response.status_code == 409
    assert store.write_count == 0


@pytest.mark.parametrize("target", ["parent", "focus"])
def test_ingest_upsert_rejects_same_clock_different_relationship_or_focus(target):
    """到着順だけで同時刻の委任関係や今回要約を上書きする回帰を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["items"][0].update(
        {
            "parent_relation": "root",
            "parent_source": "runtime-canonical-task-path",
            "parent_observed_at": current.isoformat(),
        }
    )
    data["focus_summary"] = {
        "purpose": "現行目的",
        "progress_summary": "現行進捗",
        "next_action": "現行次行動",
        "updated_at": current.isoformat(),
        "source": "manual-public-summary",
    }
    payload = {"source": "local-event-record"}
    if target == "parent":
        item = data["items"][0].copy()
        item["parent_relation"] = "delegated"
        item["parent_agent"] = "outside-parent"
        payload["items"] = [item]
    else:
        focus = data["focus_summary"].copy()
        focus["progress_summary"] = "同時刻の別進捗"
        payload["focus_summary"] = focus
    store = MemoryStore(StatusSnapshot.model_validate(data))

    response = TestClient(create_app(store)).put("/api/status/upsert", json=payload)

    assert response.status_code == 409
    assert store.write_count == 0


def test_parent_schema_rejects_self_and_cycles_but_allows_outside_parent():
    """明白な循環を保存せず、snapshot外の実parentは欠損扱いにしない。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    first = data["items"][0]
    first.update(
        {
            "parent_relation": "delegated",
            "parent_agent": "outside-parent",
            "parent_source": "explicit-delegation",
            "parent_observed_at": current.isoformat(),
        }
    )
    assert StatusSnapshot.model_validate(data).items[0].parent_agent == "outside-parent"

    first["parent_agent"] = first["agent"]
    with pytest.raises(ValueError, match="itself"):
        StatusSnapshot.model_validate(data)

    first["parent_agent"] = "second"
    data["items"].append(
        {
            "agent": "second",
            "role": "worker",
            "task": "second task",
            "status": "running",
            "observed_at": current.isoformat(),
            "parent_relation": "delegated",
            "parent_agent": first["agent"],
            "parent_source": "runtime-canonical-task-path",
            "parent_observed_at": current.isoformat(),
        }
    )
    with pytest.raises(ValueError, match="cycle"):
        StatusSnapshot.model_validate(data)


def test_instruction_summary_requires_its_public_summary_clock_for_full_snapshots():
    """初回/full PUTで指示要約だけを時刻なしに最新表示する回帰を防ぐ。"""
    data = snapshot(datetime.now(UTC))
    data["items"][0]["instruction_summary"] = "時計のない公開指示"

    with pytest.raises(ValueError, match="summary_updated_at"):
        StatusSnapshot.model_validate(data)


def test_ingest_upsert_same_content_is_idempotent():
    """同内容の確認済み再送がreceived_atやBlobを刷新する回帰を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    store = MemoryStore(StatusSnapshot.model_validate(data))
    client = TestClient(create_app(store))

    first = client.put(
        "/api/status/upsert",
        json={"source": "local-event-record", "items": data["items"]},
    )
    first_received_at = store.value.received_at
    response = client.put(
        "/api/status/upsert",
        json={"source": "local-event-record", "items": data["items"]},
    )

    assert first.status_code == 200
    assert first.json()["changed"] is True
    assert response.status_code == 200
    assert response.json()["changed"] is False
    assert response.json()["revision"] == "1"
    assert store.write_count == 1
    assert store.value.received_at == first_received_at


@pytest.mark.parametrize("target", ["item", "capacity"])
def test_ingest_upsert_rejects_older_target_observation(target):
    """遅延した部分更新が新しい対象rowやcapacityを巻き戻す回帰を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["runtime_capacity"] = {
        "scope": "root session tree",
        "observed_at": current.isoformat(),
        "state_source": "runtime-list-agents-metadata",
        "running": 1,
    }
    store = MemoryStore(StatusSnapshot.model_validate(data))
    older = current - timedelta(seconds=1)
    payload = {"source": "local-event-record"}
    if target == "item":
        item = snapshot(older)["items"][0]
        payload["items"] = [item]
    else:
        payload["runtime_capacity"] = {
            "scope": "root session tree",
            "observed_at": older.isoformat(),
            "state_source": "runtime-list-agents-metadata",
            "running": 1,
        }

    response = TestClient(create_app(store)).put("/api/status/upsert", json=payload)

    assert response.status_code == 409
    assert store.write_count == 0


@pytest.mark.parametrize("clock", ["latest_activity_at", "summary_updated_at"])
@pytest.mark.parametrize("incoming_value", [None, "older"])
def test_ingest_upsert_does_not_clear_or_rewind_independent_item_clocks(clock, incoming_value):
    """新しいitem observed_atで古いactivityや公開メモ時刻を隠す回帰を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["source"] = "ingest-upsert"
    data["items"][0][clock] = current.isoformat()
    if clock == "latest_activity_at":
        data["items"][0]["latest_activity"] = "structured-item"
    else:
        data["items"][0]["current_action"] = "新しい公開メモ"
    store = MemoryStore(StatusSnapshot.model_validate(data))
    incoming = snapshot(current + timedelta(seconds=1))["items"][0]
    if incoming_value == "older":
        incoming[clock] = (current - timedelta(seconds=1)).isoformat()
        if clock == "latest_activity_at":
            incoming["latest_activity"] = "structured-item"
        else:
            incoming["current_action"] = "古い公開メモ"

    response = TestClient(create_app(store)).put(
        "/api/status/upsert",
        json={"source": "local-event-record", "items": [incoming]},
    )

    assert response.status_code == 409
    assert store.write_count == 0


@pytest.mark.parametrize("target", ["item", "activity", "summary", "capacity"])
def test_ingest_upsert_rejects_different_content_at_the_same_target_clock(target):
    """同一clockの異なる観測を到着順だけで上書きする回帰を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    item = data["items"][0]
    item.update(
        {
            "latest_activity": "task-started",
            "latest_activity_at": current.isoformat(),
            "current_action": "現行メモ",
            "summary_updated_at": current.isoformat(),
        }
    )
    data["runtime_capacity"] = {
        "scope": "root session tree",
        "observed_at": current.isoformat(),
        "state_source": "runtime-list-agents-metadata",
        "running": 4,
        "total": 4,
    }
    store = MemoryStore(StatusSnapshot.model_validate(data))
    payload = {"source": "local-event-record"}
    if target == "capacity":
        changed_capacity = data["runtime_capacity"].copy()
        changed_capacity["running"] = 3
        payload["runtime_capacity"] = changed_capacity
    else:
        changed_item = item.copy()
        if target == "item":
            changed_item["status"] = "completed"
        elif target == "activity":
            changed_item["latest_activity"] = "task-complete"
        else:
            changed_item["current_action"] = "同時刻の別メモ"
        payload["items"] = [changed_item]

    response = TestClient(create_app(store)).put("/api/status/upsert", json=payload)

    assert response.status_code == 409
    assert store.write_count == 0


def test_full_ingest_cannot_sequentially_erase_upsert_summary_at_same_observation():
    """新しいreceived_atだけの旧full payloadがupsertメモを消す回帰を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["source"] = "ingest-upsert"
    data["items"][0].update(
        {
            "current_action": "server部分更新を実装",
            "progress_summary": "CAS検証済み",
            "summary_updated_at": current.isoformat(),
        }
    )
    store = MemoryStore(StatusSnapshot.model_validate(data))
    delayed = snapshot(current)
    delayed["source"] = "local-event-record"
    delayed["received_at"] = (current + timedelta(seconds=30)).isoformat()

    response = TestClient(create_app(store)).put("/api/status", json=delayed)

    assert response.status_code == 409
    assert store.write_count == 0
    assert store.value.items[0].current_action == "server部分更新を実装"


def test_newer_full_ingest_cannot_implicitly_delete_an_upsert_row():
    """global観測時刻だけ新しいfull PUTが部分更新済みrowを消す回帰を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["source"] = "ingest-upsert"
    data["items"].append(
        {
            "agent": "upsert-only-owner",
            "role": "実装担当",
            "task": "保持が必要な公開メモ",
            "status": "running",
            "observed_at": current.isoformat(),
            "current_action": "実データを供給",
            "summary_updated_at": current.isoformat(),
        }
    )
    store = MemoryStore(StatusSnapshot.model_validate(data))
    delayed = snapshot(current + timedelta(seconds=1))
    delayed["source"] = "local-event-record"

    response = TestClient(create_app(store)).put("/api/status", json=delayed)

    assert response.status_code == 409
    assert [item.agent for item in store.value.items] == [
        "management-status-owner",
        "upsert-only-owner",
    ]


def test_full_ingest_cannot_clear_or_rewind_capacity():
    """full PUTが独立したcapacity観測を欠損または古い値へ戻す回帰を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["source"] = "ingest-upsert"
    data["runtime_capacity"] = {
        "scope": "root session tree",
        "observed_at": current.isoformat(),
        "state_source": "runtime-list-agents-metadata",
        "running": 4,
    }
    for capacity in (
        None,
        {
            "scope": "root session tree",
            "observed_at": (current - timedelta(seconds=1)).isoformat(),
            "state_source": "runtime-list-agents-metadata",
            "running": 3,
        },
    ):
        store = MemoryStore(StatusSnapshot.model_validate(data))
        delayed = snapshot(current + timedelta(seconds=1))
        delayed["source"] = "local-event-record"
        if capacity is not None:
            delayed["runtime_capacity"] = capacity

        response = TestClient(create_app(store)).put("/api/status", json=delayed)

        assert response.status_code == 409
        assert store.write_count == 0


def test_ingest_upsert_reports_store_conflict_without_retrying():
    """Blob CAS競合を成功扱いしたり自動再送する回帰を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    updated = snapshot(current)["items"][0]
    updated["current_action"] = "競合する更新"
    updated["summary_updated_at"] = current.isoformat()
    store = ConflictingStore(StatusSnapshot.model_validate(data))

    response = TestClient(create_app(store)).put(
        "/api/status/upsert",
        json={"source": "local-event-record", "items": [updated]},
    )

    assert response.status_code == 409
    assert store.write_count == 0


def test_ingest_upsert_distinguishes_missing_snapshot_from_read_failure():
    """未初期化409とstorage障害503を同じ成功可能な状態へ潰す回帰を防ぐ。"""
    request = {
        "source": "local-event-record",
        "items": snapshot(datetime.now(UTC))["items"],
    }

    missing = TestClient(create_app(MemoryStore())).put("/api/status/upsert", json=request)
    failed = TestClient(create_app(FailingReadStore())).put("/api/status/upsert", json=request)

    assert missing.status_code == 409
    assert missing.json()["detail"] == "status snapshot is required before partial update"
    assert failed.status_code == 503
    assert failed.json()["detail"] == "status snapshot is invalid"


@pytest.mark.parametrize(
    "payload",
    [
        {"source": "local-event-record"},
        {"source": "local-event-record", "runtime_capacity": None},
        {
            "source": "local-event-record",
            "items": [
                snapshot(datetime.now(UTC))["items"][0],
                snapshot(datetime.now(UTC))["items"][0],
            ],
        },
    ],
)
def test_ingest_upsert_rejects_invalid_targets(payload):
    """空更新・null capacity・重複agentを曖昧なno-opとして受け付けない。"""
    response = TestClient(
        create_app(MemoryStore(StatusSnapshot.model_validate(snapshot(datetime.now(UTC)))))
    ).put("/api/status/upsert", json=payload)

    assert response.status_code == 422


def test_full_ingest_uses_store_revision_and_reports_conflict():
    """既存full PUTが部分更新と競合してlost updateを起こす回帰を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["source"] = "local-event-record"
    changed = snapshot(current + timedelta(seconds=1))
    changed["source"] = "local-event-record"
    store = ConflictingStore(StatusSnapshot.model_validate(data))

    response = TestClient(create_app(store)).put("/api/status", json=changed)

    assert response.status_code == 409
    assert store.write_count == 0


def test_runtime_capacity_keeps_turn_counts_and_limit_sources_separate():
    """task件数をruntimeの実行枠へ混ぜ、上限の根拠を失う回帰を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["runtime_capacity"] = {
        "scope": "/root session tree",
        "observed_at": current.isoformat(),
        "state_source": "runtime-list-agents-metadata",
        "limit_source": "runtime-instructions",
        "running": 4,
        "idle": 0,
        "completed": 2,
        "total": 6,
        "max_concurrent_agents": 8,
    }

    parsed = StatusSnapshot.model_validate(data)

    assert parsed.runtime_capacity is not None
    assert parsed.runtime_capacity.running == 4
    assert parsed.runtime_capacity.max_concurrent_agents == 8


@pytest.mark.parametrize(
    "capacity",
    [
        {"scope": "/root", "observed_at": "2026-09-12T16:55:23Z", "running": 4},
        {
            "scope": "/root",
            "observed_at": "2026-09-12T16:55:23Z",
            "state_source": "runtime-list-agents-metadata",
            "running": 4,
            "total": 3,
        },
        {
            "scope": "/root",
            "observed_at": "2026-09-12T16:55:23Z",
            "state_source": "runtime-list-agents-metadata",
            "limit_source": "runtime-instructions",
            "running": 9,
            "max_concurrent_agents": 8,
        },
    ],
)
def test_runtime_capacity_rejects_unattributed_or_inconsistent_counts(capacity):
    """未知状態を0扱いし、出所なしのcapacityを公開する回帰を防ぐ。"""
    data = snapshot(datetime.now(UTC))
    data["runtime_capacity"] = capacity

    with pytest.raises(ValueError):
        StatusSnapshot.model_validate(data)


def test_session_tree_and_history_upsert_preserve_rows_and_return_only_supplied_fields():
    """current inventoryと履歴を全体GETなしで保存し、限定receiptだけ返す。"""
    now = datetime.now(UTC)
    store = MemoryStore(StatusSnapshot.model_validate(snapshot(now)))
    client = TestClient(create_app(store))
    response = client.put(
        "/api/status/upsert",
        json={
            "source": "local-event-record",
            "session_tree": session_tree(now),
            "known_history": known_history(now),
        },
    )
    assert response.status_code == 200
    assert response.json()["session_tree"]["covered_agents"] == ["management-status-owner"]
    assert response.json()["known_history"]["entries"][0]["agent"] == "old-worker"
    assert response.json()["items"] == []
    assert store.value.session_tree is not None
    assert store.value.known_history is not None


def test_session_tree_clock_rejects_regression_and_same_clock_conflict():
    """遅延manifestや同時刻の異なるinventoryがcurrent treeを巻き戻す回帰を防ぐ。"""
    now = datetime.now(UTC)
    data = snapshot(now)
    data["source"] = "ingest-upsert"
    data["session_tree"] = session_tree(now)
    store = MemoryStore(StatusSnapshot.model_validate(data))
    client = TestClient(create_app(store))
    older = session_tree(now - timedelta(seconds=1))
    assert (
        client.put(
            "/api/status/upsert", json={"source": "local-event-record", "session_tree": older}
        ).status_code
        == 409
    )
    different = session_tree(now)
    different["scope"] = "different"
    assert (
        client.put(
            "/api/status/upsert", json={"source": "local-event-record", "session_tree": different}
        ).status_code
        == 409
    )


def test_full_put_cannot_clear_session_tree_or_history():
    """旧full publisherが新しいmanifest/historyを暗黙削除する回帰を防ぐ。"""
    now = datetime.now(UTC)
    data = snapshot(now)
    data["source"] = "ingest-upsert"
    data["session_tree"] = session_tree(now)
    data["known_history"] = known_history(now)
    client = TestClient(create_app(MemoryStore(StatusSnapshot.model_validate(data))))
    incoming = snapshot(now + timedelta(seconds=1))
    incoming["source"] = "local-event-record"
    assert client.put("/api/status", json=incoming).status_code == 409


def test_session_tree_rejects_cycle_and_uncovered_public_row():
    """循環treeや公開rowのないcoverageでUIが壊れる回帰を防ぐ。"""
    now = datetime.now(UTC)
    data = snapshot(now)
    bad = session_tree(now)
    bad["nodes"] = [
        {"agent": "management-status-owner", "parent_agent": None},
        {"agent": "a", "parent_agent": "b"},
        {"agent": "b", "parent_agent": "a"},
    ]
    data["session_tree"] = bad
    with pytest.raises(ValueError):
        StatusSnapshot.model_validate(data)
    bad = session_tree(now)
    bad["nodes"].append({"agent": "missing", "parent_agent": "management-status-owner"})
    bad["covered_agents"].append("missing")
    data["session_tree"] = bad
    with pytest.raises(ValueError):
        StatusSnapshot.model_validate(data)


@pytest.mark.parametrize("available", [3, 5])
def test_runtime_capacity_rejects_incorrect_derived_available(available):
    """根拠のない空き値を上限とrunningから独立に公開する回帰を防ぐ。"""
    now = datetime.now(UTC)
    data = snapshot(now)
    data["runtime_capacity"] = {
        "scope": "root session tree",
        "observed_at": now.isoformat(),
        "state_source": "runtime-list-agents-metadata",
        "limit_source": "runtime-instructions",
        "running": 4,
        "idle": 0,
        "completed": 1,
        "total": 5,
        "max_concurrent_agents": 8,
        "available": available,
        "availability_source": "derived-running-limit",
        "availability_definition": "max-concurrent-minus-running",
    }
    with pytest.raises(ValueError):
        StatusSnapshot.model_validate(data)


@pytest.mark.parametrize(
    "entries",
    [
        [
            {
                "agent": "old",
                "parent_agent": "old",
                "status": "completed",
                "source": "pm-recorded-completed-work-unit",
            }
        ],
        [
            {
                "agent": "old-a",
                "parent_agent": "old-b",
                "status": "completed",
                "source": "pm-recorded-completed-work-unit",
            },
            {
                "agent": "old-b",
                "parent_agent": "old-a",
                "status": "completed",
                "source": "pm-recorded-completed-work-unit",
            },
        ],
    ],
)
def test_known_history_rejects_self_parent_and_cycle(entries):
    """破損した履歴関係でtree rendererが再帰停止しない回帰を防ぐ。"""
    now = datetime.now(UTC)
    data = snapshot(now)
    data["known_history"] = {"recorded_at": now.isoformat(), "entries": entries}
    with pytest.raises(ValueError):
        StatusSnapshot.model_validate(data)
