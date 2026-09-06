from datetime import UTC, datetime

import pytest
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobClient
from ops_status.models import StatusSnapshot
from ops_status.store import BlobSnapshotStore


class FakeBlobClient:
    def __init__(self):
        self.uploads = []
        self.download_error = None

    def upload_blob(self, data, **kwargs):
        self.uploads.append((data, kwargs))

    def download_blob(self, **_kwargs):
        if self.download_error:
            raise self.download_error
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
