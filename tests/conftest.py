import os
from dotenv import load_dotenv
load_dotenv()
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
import pytest  # noqa: E402
from src.db.session import SessionLocal  # noqa: E402
from src.db.models import Base  # noqa: E402
from src.events.bus import r  # noqa: E402
from src.db.session import engine  # noqa: E402
from scripts.seed import seed  # noqa: E402


@pytest.fixture(scope="session")
def setup_db():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)

@pytest.fixture
def db_session(setup_db):
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()

@pytest.fixture
def flush_redis():
    r.flushdb()
    yield
    r.flushdb()
    
@pytest.fixture
def seeded_db(db_session, flush_redis):
    seed()
    return db_session