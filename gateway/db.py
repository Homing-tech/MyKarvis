"""SQLite 存储：业务表 + 会话历史 + FTS5 全文检索 + 每日 Markdown 记忆。"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from config import settings

_DB_PATH = settings.data_dir / "karvis.db"
_LOCAL = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS workout_log (
  id INTEGER PRIMARY KEY,
  log_date TEXT NOT NULL,
  plan TEXT,
  exercises TEXT,
  cardio TEXT,
  raw_input TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS workout_feedback (
  id INTEGER PRIMARY KEY,
  for_date TEXT NOT NULL,
  report_date TEXT NOT NULL,
  soreness TEXT,
  pain TEXT,
  sleep TEXT,
  energy TEXT,
  raw_input TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS mood_log (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  event TEXT,
  feeling TEXT,
  body TEXT,
  thought TEXT,
  intensity INTEGER,
  protective_fn TEXT,
  fact_or_assumption TEXT,
  evidence_for TEXT,
  evidence_against TEXT,
  reframe TEXT,
  micro_action TEXT,
  action_done INTEGER,
  naming TEXT,
  raw_input TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS inbox_item (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  kind TEXT,
  title TEXT,
  detail TEXT,
  tags TEXT,
  status TEXT,
  due_date TEXT,
  last_reminded TEXT,
  raw_input TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS chat_log (
  id INTEGER PRIMARY KEY,
  agent_key TEXT NOT NULL,
  user_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_agent_user ON chat_log(agent_key, user_id, id);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
  path, content, tokenize='unicode61'
);
"""


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def get_conn() -> sqlite3.Connection:
    """线程内复用连接（FastAPI 的线程池里每个线程一个）。"""
    conn = getattr(_LOCAL, "conn", None)
    if conn is None:
        conn = sqlite3.connect(_DB_PATH, timeout=15, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        conn.commit()
        _LOCAL.conn = conn
    return conn


def init_db() -> None:
    get_conn()
    (settings.data_dir / "memory").mkdir(parents=True, exist_ok=True)


# ---------------- 会话历史 ----------------
def append_chat(agent_key: str, user_id: str, role: str, content: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO chat_log(agent_key,user_id,role,content,created_at) VALUES(?,?,?,?,?)",
        (agent_key, user_id, role, content, now_str()),
    )
    conn.commit()


def recent_chat(agent_key: str, user_id: str, limit: int = 12) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT role,content FROM chat_log WHERE agent_key=? AND user_id=? "
        "ORDER BY id DESC LIMIT ?",
        (agent_key, user_id, limit),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def clear_chat(agent_key: str, user_id: str) -> int:
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM chat_log WHERE agent_key=? AND user_id=?", (agent_key, user_id)
    )
    conn.commit()
    return cur.rowcount


# ---------------- 通用写入 / 查询 ----------------
def insert(table: str, data: dict) -> int:
    conn = get_conn()
    cols = ",".join(data.keys())
    marks = ",".join("?" * len(data))
    cur = conn.execute(
        f"INSERT INTO {table}({cols}) VALUES({marks})", tuple(data.values())
    )
    conn.commit()
    return cur.lastrowid


def query(sql: str, args: tuple = ()) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def update(table: str, row_id: int, data: dict) -> None:
    conn = get_conn()
    sets = ",".join(f"{k}=?" for k in data)
    conn.execute(
        f"UPDATE {table} SET {sets} WHERE id=?", (*data.values(), row_id)
    )
    conn.commit()


# ---------------- 每日 Markdown 记忆（沿用 KarvisPi 结构）----------------
def memory_dir() -> Path:
    d = settings.data_dir / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_daily_memory(agent_key: str, text: str) -> Path:
    """写入 data/memory/{agent_key}/YYYY-MM-DD.md，供人直接阅读与备份。"""
    d = memory_dir() / agent_key
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{today()}.md"
    stamp = datetime.now().strftime("%H:%M")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n\n## {stamp}\n\n{text}\n")
    index_memory(str(path.relative_to(settings.data_dir)), text)
    return path


def read_recent_memory(agent_key: str, days: int = 3) -> str:
    from datetime import timedelta

    d = memory_dir() / agent_key
    out = []
    for i in range(days):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        p = d / f"{day}.md"
        if p.exists():
            out.append(f"### {day}\n{p.read_text(encoding='utf-8')}")
    return "\n\n".join(out)


def index_memory(path: str, content: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM memory_fts WHERE path=?", (path,))
    conn.execute("INSERT INTO memory_fts(path,content) VALUES(?,?)", (path, content))
    conn.commit()


def search_memory(keyword: str, limit: int = 8) -> list[dict]:
    """中文按 bigram 拆分后 OR 检索，避免 unicode61 对中文整串不分词的问题。"""
    conn = get_conn()
    terms = [keyword[i : i + 2] for i in range(len(keyword) - 1)] or [keyword]
    clause = " OR ".join(['"{}"'.format(t.replace('"', "")) for t in terms[:12]])
    sql = (
        "SELECT path, snippet(memory_fts, 1, '【', '】', '…', 12) AS snip "
        f"FROM memory_fts WHERE memory_fts MATCH ? ORDER BY rank LIMIT {limit}"
    )
    try:
        rows = conn.execute(sql, (clause,)).fetchall()
    except sqlite3.OperationalError:
        rows = conn.execute(
            "SELECT path, content AS snip FROM memory_fts WHERE content LIKE ? LIMIT ?",
            (f"%{keyword}%", limit),
        ).fetchall()
    return [dict(r) for r in rows]


def dump_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
