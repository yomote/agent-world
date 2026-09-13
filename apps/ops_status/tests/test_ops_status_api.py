import json
from datetime import UTC, datetime, timedelta

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
