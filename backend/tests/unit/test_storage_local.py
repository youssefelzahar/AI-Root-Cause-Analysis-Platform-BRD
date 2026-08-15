import hashlib
import uuid

import pytest

from app.storage.base import FileTooLarge, ObjectNotFound
from app.storage.local import LocalStorageBackend


async def _chunks(data: bytes, size: int = 8):
    for index in range(0, len(data), size):
        yield data[index : index + size]


@pytest.fixture
def storage(tmp_path) -> LocalStorageBackend:
    return LocalStorageBackend(tmp_path / "root", tmp_path / "tmp")


@pytest.mark.asyncio
async def test_save_stream_roundtrip_and_checksum(storage: LocalStorageBackend) -> None:
    payload = b"date,revenue\n2026-01-01,100\n" * 50
    stored = await storage.save_stream("c/2026/01/data.csv", _chunks(payload), max_bytes=1_000_000)

    assert stored.size_bytes == len(payload)
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    with storage.open(stored.key) as handle:
        assert handle.read() == payload


@pytest.mark.asyncio
async def test_oversize_upload_aborts_and_leaves_nothing_behind(storage: LocalStorageBackend) -> None:
    key = "c/2026/01/big.csv"
    with pytest.raises(FileTooLarge):
        await storage.save_stream(key, _chunks(b"x" * 500), max_bytes=100)

    # Neither the final object nor a partial file may survive.
    assert not storage.exists(key)
    assert list(storage.tmp_dir.glob("*.part")) == []


def test_build_key_never_contains_the_original_filename(storage: LocalStorageBackend) -> None:
    """PRD principle 2: the physical path is UUID-derived."""
    company_id, dataset_id = uuid.uuid4(), uuid.uuid4()
    key = storage.build_key(company_id=company_id, dataset_id=dataset_id, extension=".csv")

    assert str(dataset_id) in key
    assert str(company_id) in key
    for fragment in ("payroll", "secret", "Q3-report"):
        assert fragment not in key


def test_path_traversal_is_refused(storage: LocalStorageBackend) -> None:
    from app.storage.base import StorageError

    with pytest.raises(StorageError):
        storage.exists("../../../etc/passwd")


def test_delete_is_idempotent_and_reports_when_asked(storage: LocalStorageBackend) -> None:
    storage.delete("missing/key.csv", missing_ok=True)
    with pytest.raises(ObjectNotFound):
        storage.delete("missing/key.csv", missing_ok=False)
