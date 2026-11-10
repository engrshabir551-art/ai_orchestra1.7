"""SQLite storage. One short-lived connection per call keeps this safe across Streamlit threads."""
import os
import sqlite3
import time
from contextlib import contextmanager

DB_PATH = os.environ.get("ORCHESTRA_DB", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "orchestra.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, salt TEXT, pw_hash TEXT, role TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS ledger(
  id INTEGER PRIMARY KEY, ts REAL, mode TEXT, wallet TEXT, kind TEXT, amount_cents INTEGER,
  ref TEXT, release_id INTEGER, memo TEXT, verified INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS releases(
  id INTEGER PRIMARY KEY, ts REAL, mode TEXT, status TEXT, payee_name TEXT, bsb TEXT, account TEXT, payid TEXT,
  amount_cents INTEGER, reference TEXT, memo TEXT, created_by TEXT, approved_by TEXT, approved_ts REAL,
  settled_ts REAL, channel TEXT, batch_id TEXT);
CREATE TABLE IF NOT EXISTS payees(id INTEGER PRIMARY KEY, name TEXT, bsb TEXT, account TEXT, payid TEXT, first_seen REAL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts REAL, actor TEXT, action TEXT, detail TEXT, prev_hash TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS gold_bars(
  id INTEGER PRIMARY KEY, serial TEXT UNIQUE, weight_g REAL, purity REAL, location TEXT, custodian TEXT,
  status TEXT, cert_ref TEXT, added_ts REAL);
CREATE TABLE IF NOT EXISTS gold_moves(
  id INTEGER PRIMARY KEY, ts REAL, bar_id INTEGER, kind TEXT, to_location TEXT, buyer TEXT, price_aud_cents INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS gold_prices(id INTEGER PRIMARY KEY, ts REAL, usd_per_oz REAL, usd_aud REAL, source TEXT);
CREATE TABLE IF NOT EXISTS agents(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE, grp TEXT, role TEXT, prompt TEXT, tools TEXT, enabled INTEGER DEFAULT 1, custom INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS memory(id INTEGER PRIMARY KEY, ts REAL, agent TEXT, kind TEXT, content TEXT);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, ts REAL, agent TEXT, title TEXT, status TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY, ts REAL, agent TEXT, objective TEXT, target TEXT, hours REAL, tick_minutes REAL,
  status TEXT, ends REAL, last_tick REAL, report TEXT);
CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY, release_id INTEGER, approver TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS budgets(category TEXT PRIMARY KEY, monthly_cents INTEGER);
CREATE TABLE IF NOT EXISTS recurring(
  id INTEGER PRIMARY KEY, mode TEXT, payee_name TEXT, method TEXT, bsb TEXT DEFAULT '', account TEXT DEFAULT '', payid TEXT DEFAULT '',
  biller_code TEXT DEFAULT '', crn TEXT DEFAULT '', amount_cents INTEGER, purpose TEXT DEFAULT '', category TEXT DEFAULT '', invoice_ref TEXT DEFAULT '',
  frequency TEXT, start_date TEXT, k INTEGER DEFAULT 0, next_due TEXT, end_date TEXT DEFAULT '', max_count INTEGER DEFAULT 0, drafted INTEGER DEFAULT 0,
  status TEXT DEFAULT 'ACTIVE', last_error TEXT DEFAULT '', created_by TEXT, created_ts REAL, last_release_id INTEGER);
CREATE TABLE IF NOT EXISTS checkpoints(id INTEGER PRIMARY KEY, job_id INTEGER, ts REAL, note TEXT);
"""


_ready = set()  # database files already prepared in this process (folder created, WAL switched on)


def _prepare(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path, timeout=30)
    try:
        c.execute("PRAGMA journal_mode=WAL")  # stored in the file, so it only needs setting once
    finally:
        c.close()
    _ready.add(path)
    return path


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH if DB_PATH in _ready else _prepare(DB_PATH), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA synchronous=NORMAL")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


MIGRATIONS = [
    ("releases", "method", "TEXT DEFAULT 'BANK'"), ("releases", "biller_code", "TEXT DEFAULT ''"), ("releases", "crn", "TEXT DEFAULT ''"),
    ("releases", "category", "TEXT DEFAULT ''"), ("releases", "invoice_ref", "TEXT DEFAULT ''"), ("releases", "due_date", "TEXT DEFAULT ''"),
    ("releases", "approvals_needed", "INTEGER DEFAULT 1"),
    ("payees", "method", "TEXT DEFAULT 'BANK'"), ("payees", "biller_code", "TEXT DEFAULT ''"), ("payees", "crn", "TEXT DEFAULT ''"),
    ("payees", "category", "TEXT DEFAULT ''"),
    ("users", "totp_secret", "TEXT DEFAULT ''"),
    ("releases", "sent_ts", "REAL"), ("releases", "schedule_id", "INTEGER"), ("releases", "bank_ref", "TEXT DEFAULT ''"),
]


def init():
    """Create tables and add any columns missing from an older database (safe to run every start)."""
    with conn() as c:
        c.executescript(SCHEMA)
        for table, col, decl in MIGRATIONS:
            cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        for table in ("releases", "payees"):  # rows saved before payment methods existed
            c.execute(f"UPDATE {table} SET method='PAYID' WHERE method='BANK' AND (bsb IS NULL OR bsb='') AND payid IS NOT NULL AND payid!=''")


def q(sql, args=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def q1(sql, args=()):
    r = q(sql, args)
    return r[0] if r else None


def x(sql, args=()):
    with conn() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def now() -> float:
    return time.time()


def get_setting(key, default=None):
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def set_setting(key, value):
    x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
