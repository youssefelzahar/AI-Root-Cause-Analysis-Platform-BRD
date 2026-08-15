"""Shared fixtures.

Unit tests need no database. Integration tests run against SQLite, which works
only because the models deliberately use ``Uuid`` and
``JSON().with_variant(JSONB, "postgresql")`` rather than PostgreSQL-only types.
Set TEST_DATABASE_URL to exercise the same suite against real PostgreSQL.
"""

import os
import uuid
from pathlib import Path

import pytest

# Settings are read at import time, so the environment must be prepared before
# anything from `app` is imported.
_TMP = Path(__file__).resolve().parent / ".pytest-data"
_TMP.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", os.environ.get("TEST_DATABASE_URL", "sqlite://"))
os.environ.setdefault("STORAGE_LOCAL_ROOT", str(_TMP / "uploads"))
os.environ.setdefault("STORAGE_TMP_DIR", str(_TMP / "tmp"))
os.environ.setdefault("DUCKDB_TEMP_DIR", str(_TMP / "duckdb"))
# Inline profiling so assertions never race the background task.
os.environ.setdefault("PROFILING_ASYNC", "false")
os.environ.setdefault("ENCRYPTION_KEY", "test-encryption-key-for-the-suite-only")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.api.deps import get_db  # noqa: E402
from app.core.config import DEFAULT_COMPANY_ID, DEFAULT_USER_ID, settings  # noqa: E402
from app.db.models import Base, Company, User  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def storage_root(tmp_path, monkeypatch):
    """Point storage at a per-test directory."""
    from app.services import storage_service

    root = tmp_path / "uploads"
    monkeypatch.setattr(settings, "storage_local_root", str(root))
    monkeypatch.setattr(settings, "storage_tmp_dir", str(tmp_path / "tmp"))
    storage_service.reset_storage_cache()
    yield root
    storage_service.reset_storage_cache()


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(db_engine, monkeypatch):
    factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)

    # Background jobs open their own session via session_scope() rather than
    # reusing the request's, so overriding get_db alone is not enough - the
    # module-level factory has to point at the test database too.
    import app.db.session as db_module

    monkeypatch.setattr(db_module, "SessionLocal", factory)

    session = factory()
    # Mirror what migration 0002 seeds.
    session.add(Company(id=DEFAULT_COMPANY_ID, name="Default Company", slug="default"))
    session.add(
        User(
            id=DEFAULT_USER_ID,
            company_id=DEFAULT_COMPANY_ID,
            email="analyst@example.com",
            display_name="Default Analyst",
        )
    )
    session.commit()
    yield session
    session.close()


@pytest.fixture
def other_company(db_session):
    """A second tenant, for isolation tests."""
    company_id = uuid.uuid4()
    db_session.add(Company(id=company_id, name="Other Company", slug="other"))
    db_session.commit()
    return company_id


@pytest.fixture
def client(db_session, storage_root):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- data fixtures -------------------------------------------------------
CLEAN_CSV = """date,region,product,customer,revenue,cost,quantity
2026-06-01,Cairo,Product A,Enterprise,1200,800,10
2026-06-02,Cairo,Product B,SMB,950,600,8
2026-06-03,Giza,Product A,Enterprise,1100,700,9
2026-06-04,Giza,Product B,SMB,1050,650,7
2026-06-05,Cairo,Product A,Enterprise,1300,850,11
2026-06-06,Alexandria,Product C,SMB,890,400,5
2026-06-07,Cairo,Product A,Enterprise,1010,300,4
2026-06-08,Giza,Product B,Enterprise,980,620,8
2026-06-09,Cairo,Product A,SMB,1150,720,9
2026-06-10,Alexandria,Product C,Enterprise,1220,500,6
"""

# revenue arrives as formatted text - the PRD section 10 example.
MESSY_CSV = """date,region,revenue
2026-06-01,Cairo,"$1,200"
2026-06-02,Cairo,950.50
2026-06-03,Giza,"1,050.25"
2026-06-04,Giza,(500)
2026-06-05,Cairo,not-a-number
2026-06-06,Alexandria,1300
2026-06-07,Cairo,1010
2026-06-08,Giza,980
2026-06-09,Cairo,1150
2026-06-10,Alexandria,1220
"""

TEXT_ONLY_CSV = """name,notes
alpha,first
beta,second
gamma,third
"""


@pytest.fixture
def clean_csv_bytes() -> bytes:
    return CLEAN_CSV.encode()


@pytest.fixture
def messy_csv_bytes() -> bytes:
    return MESSY_CSV.encode()


@pytest.fixture
def text_only_csv_bytes() -> bytes:
    return TEXT_ONLY_CSV.encode()
