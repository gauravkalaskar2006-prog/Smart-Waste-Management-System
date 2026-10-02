import sqlite3
from pathlib import Path
from typing import Optional

from flask import current_app, g


def _db_path() -> str:
    # Keep DB in project root by default
    p = current_app.config.get("DATABASE")
    if p:
        return str(p)
    return str(Path(current_app.root_path).parent / "database.db")


def get_db() -> sqlite3.Connection:
    """Get a per-request sqlite connection stored in Flask g."""
    if "db" not in g:
        conn = sqlite3.connect(_db_path(), detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        g.db = conn
    return g.db


def close_db(e: Optional[BaseException] = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    """Create tables if they don't exist (STRICT SCHEMA)."""
    db = get_db()
    schema_path = Path(current_app.root_path) / "schema.sql"
    db.executescript(schema_path.read_text(encoding="utf-8"))
    db.commit()




