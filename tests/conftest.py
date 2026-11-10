import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["ORCHESTRA_DB"] = os.path.join(tempfile.mkdtemp(), "suite.db")
import pytest
from core import db, agents, security


@pytest.fixture(autouse=True, scope="module")
def clean_db():
    for ext in ("", "-wal", "-shm"):
        try:
            os.remove(db.DB_PATH + ext)
        except FileNotFoundError:
            pass
    db.init(); agents.seed_agents(); db.set_setting("mode", "TEST"); db.set_setting("ceiling_cents", str(10**11))
    security.create_user("admin", "supersecret123", "admin")
    yield
