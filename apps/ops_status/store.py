import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .models import StatusResponse, StatusSnapshot

STALE_AFTER_SECONDS = 120


class SnapshotStoreError(Exception):
    pass


class SnapshotConflictError(SnapshotStoreError):
    pass


@dataclass(frozen=True)
class VersionedSnapshot:
    snapshot: StatusSnapshot
    revision: str


class SnapshotStore(Protocol):
    def read(self) -> StatusSnapshot: ...

    def read_versioned(self) -> VersionedSnapshot: ...

    def write(self, snapshot: StatusSnapshot) -> None: ...

    def write_if_revision(self, snapshot: StatusSnapshot, expected_revision: str | None) -> str: ...


class FileSnapshotStore:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> StatusSnapshot:
        return self.read_versioned().snapshot

    def read_versioned(self) -> VersionedSnapshot:
        payload = self.path.read_bytes()
        return VersionedSnapshot(
            snapshot=StatusSnapshot.model_validate_json(payload),
            revision=hashlib.sha256(payload).hexdigest(),
        )

    def write(self, snapshot: StatusSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.{os.getpid()}.tmp")
        temporary.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def write_if_revision(self, snapshot: StatusSnapshot, expected_revision: str | None) -> str:
        try:
            actual_revision = self.read_versioned().revision
        except FileNotFoundError:
            actual_revision = None
        if actual_revision != expected_revision:
            raise SnapshotConflictError("snapshot changed before write")
        self.write(snapshot)
        return self.read_versioned().revision


class BlobSnapshotStore:
    def __init__(self, blob_url: str, managed_identity_client_id: str):
        from azure.identity import ManagedIdentityCredential
        from azure.storage.blob import BlobClient

        credential = ManagedIdentityCredential(client_id=managed_identity_client_id)
        self.client = BlobClient.from_blob_url(blob_url, credential=credential, retry_total=0)

    def read(self) -> StatusSnapshot:
        return self.read_versioned().snapshot

    def read_versioned(self) -> VersionedSnapshot:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            download = self.client.download_blob(max_concurrency=1)
            payload = download.readall()
            revision = download.properties.etag
            if not isinstance(revision, str) or not revision:
                raise SnapshotStoreError("Blob snapshot revision is unavailable")
            return VersionedSnapshot(StatusSnapshot.model_validate_json(payload), revision)
        except ResourceNotFoundError as error:
            raise FileNotFoundError("Blob snapshot has not been created") from error
        except SnapshotStoreError:
            raise
        except Exception as error:
            raise SnapshotStoreError("Blob snapshot read failed") from error

    def write(self, snapshot: StatusSnapshot) -> None:
        from azure.storage.blob import ContentSettings

        try:
            self.client.upload_blob(
                snapshot.model_dump_json(indent=2).encode(),
                overwrite=True,
                content_settings=ContentSettings(content_type="application/json"),
            )
        except Exception as error:
            raise SnapshotStoreError("Blob snapshot write failed") from error

    def write_if_revision(self, snapshot: StatusSnapshot, expected_revision: str | None) -> str:
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceExistsError, ResourceModifiedError
        from azure.storage.blob import ContentSettings

        options = (
            {"overwrite": False}
            if expected_revision is None
            else {
                "overwrite": True,
                "etag": expected_revision,
                "match_condition": MatchConditions.IfNotModified,
            }
        )
        try:
            result = self.client.upload_blob(
                snapshot.model_dump_json(indent=2).encode(),
                content_settings=ContentSettings(content_type="application/json"),
                **options,
            )
            revision = result.get("etag") if isinstance(result, dict) else None
            if not isinstance(revision, str) or not revision:
                raise SnapshotStoreError("Blob snapshot write revision is unavailable")
            return revision
        except (ResourceExistsError, ResourceModifiedError) as error:
            raise SnapshotConflictError("snapshot changed before write") from error
        except SnapshotStoreError:
            raise
        except Exception as error:
            raise SnapshotStoreError("Blob snapshot write failed") from error


def configured_store() -> SnapshotStore:
    blob_url = os.environ.get("AGENT_WORLD_STATUS_BLOB_URL")
    if blob_url:
        client_id = os.environ.get("AZURE_CLIENT_ID")
        if not client_id:
            raise RuntimeError("AZURE_CLIENT_ID is required for Blob status storage")
        return BlobSnapshotStore(blob_url, client_id)
    configured = os.environ.get("AGENT_WORLD_STATUS_SNAPSHOT")
    path = Path(configured) if configured else Path("artifacts/status/current.json")
    return FileSnapshotStore(path)


def status_response(snapshot: StatusSnapshot, now: datetime | None = None) -> StatusResponse:
    current = now or datetime.now(UTC)
    received = snapshot.received_at.astimezone(UTC)
    age_seconds = max(0, int((current - received).total_seconds()))
    return StatusResponse(
        **snapshot.model_dump(), stale=age_seconds > STALE_AFTER_SECONDS, age_seconds=age_seconds
    )
