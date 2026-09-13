from datetime import UTC, datetime

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
from azure.storage.blob import BlobClient
from ops_status.models import StatusSnapshot
from ops_status.store import BlobSnapshotStore, SnapshotConflictError


class FakeDownload:
    def __init__(self, payload: bytes, etag: str):
        self.payload = payload
        self.properties = type("Properties", (), {"etag": etag})()

    def readall(self):
        return self.payload


class FakeBlobClient:
    def __init__(self):
        self.uploads = []
        self.download_error = None
        self.download = None
        self.upload_result = {"etag": '"revision-2"'}
        self.upload_error = None

    def upload_blob(self, data, **kwargs):
        if self.upload_error:
            raise self.upload_error
        self.uploads.append((data, kwargs))
        return self.upload_result

    def download_blob(self, **_kwargs):
        if self.download_error:
            raise self.download_error
        if self.download:
            return self.download
        raise AssertionError("download was not configured")


def snapshot() -> StatusSnapshot:
    now = datetime.now(UTC)
    return StatusSnapshot.model_validate(
        {
            "schema_version": 1,
            "source": "local-event-record",
            "observed_at": now,
            "received_at": now,
            "items": [],
        }
    )


def test_blob_client_disables_sdk_retries(monkeypatch):
    """応答不明のBlob writeをSDK既定retryで再送する回帰を防ぐ。"""
    captured = {}
    fake = FakeBlobClient()

    def from_blob_url(url, **kwargs):
        captured.update(url=url, **kwargs)
        return fake

    monkeypatch.setattr(BlobClient, "from_blob_url", from_blob_url)
    store = BlobSnapshotStore("https://example.blob.core.windows.net/status/current.json", "client")
    store.write(snapshot())

    assert captured["retry_total"] == 0
    assert len(fake.uploads) == 1
    assert fake.uploads[0][1]["overwrite"] is True
    assert fake.uploads[0][1]["content_settings"].content_type == "application/json"


def test_missing_blob_is_an_empty_store(monkeypatch):
    """初回ingest前のBlob 404を壊れたstoreとして固定する回帰を防ぐ。"""
    fake = FakeBlobClient()
    fake.download_error = ResourceNotFoundError("missing")
    monkeypatch.setattr(BlobClient, "from_blob_url", lambda *_args, **_kwargs: fake)
    store = BlobSnapshotStore("https://example.blob.core.windows.net/status/current.json", "client")

    with pytest.raises(FileNotFoundError):
        store.read()


def test_blob_read_returns_etag_and_conditional_write_uses_it(monkeypatch):
    """server側mergeとfull PUTが同じBlob ETag CASを使うことを保証する。"""
    fake = FakeBlobClient()
    value = snapshot()
    fake.download = FakeDownload(value.model_dump_json().encode(), '"revision-1"')
    monkeypatch.setattr(BlobClient, "from_blob_url", lambda *_args, **_kwargs: fake)
    store = BlobSnapshotStore("https://example.blob.core.windows.net/status/current.json", "client")

    versioned = store.read_versioned()
    revision = store.write_if_revision(value, versioned.revision)

    assert versioned.snapshot == value
    assert versioned.revision == '"revision-1"'
    assert revision == '"revision-2"'
    assert fake.uploads[0][1]["overwrite"] is True
    assert fake.uploads[0][1]["etag"] == '"revision-1"'
    assert fake.uploads[0][1]["match_condition"] is MatchConditions.IfNotModified


def test_blob_conditional_write_reports_conflict_without_sdk_retry(monkeypatch):
    """412競合を503や暗黙retryへ変えてlost updateを隠す回帰を防ぐ。"""
    fake = FakeBlobClient()
    fake.upload_error = ResourceModifiedError("changed")
    monkeypatch.setattr(BlobClient, "from_blob_url", lambda *_args, **_kwargs: fake)
    store = BlobSnapshotStore("https://example.blob.core.windows.net/status/current.json", "client")

    with pytest.raises(SnapshotConflictError):
        store.write_if_revision(snapshot(), '"revision-1"')

    assert fake.uploads == []


def test_blob_initial_conditional_write_does_not_overwrite_a_racing_create(monkeypatch):
    """初回full PUT同士の競合でも既存Blobをoverwriteしないことを保証する。"""
    fake = FakeBlobClient()
    monkeypatch.setattr(BlobClient, "from_blob_url", lambda *_args, **_kwargs: fake)
    store = BlobSnapshotStore("https://example.blob.core.windows.net/status/current.json", "client")

    revision = store.write_if_revision(snapshot(), None)

    assert revision == '"revision-2"'
    assert fake.uploads[0][1]["overwrite"] is False


def test_blob_initial_conditional_write_reports_racing_create(monkeypatch):
    """初回read後に作られたBlobを上書きせず競合として返す。"""
    fake = FakeBlobClient()
    fake.upload_error = ResourceExistsError("created")
    monkeypatch.setattr(BlobClient, "from_blob_url", lambda *_args, **_kwargs: fake)
    store = BlobSnapshotStore("https://example.blob.core.windows.net/status/current.json", "client")

    with pytest.raises(SnapshotConflictError):
        store.write_if_revision(snapshot(), None)

    assert fake.uploads == []
