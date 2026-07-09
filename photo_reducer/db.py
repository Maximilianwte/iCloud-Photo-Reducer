"""SQLite state store. Lives at ./state.db, never inside the archive folder
(the archive gets moved to an external drive; state must survive that)."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "state.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    uuid TEXT PRIMARY KEY,
    kind TEXT NOT NULL,              -- 'photo' | 'video'
    date TEXT NOT NULL,              -- ISO8601 local capture time
    original_filesize INTEGER,
    width INTEGER,
    height INTEGER,
    duration REAL,                   -- seconds, videos only
    is_favorite INTEGER NOT NULL DEFAULT 0,
    is_edited INTEGER NOT NULL DEFAULT 0,
    is_hidden INTEGER NOT NULL DEFAULT 0,
    in_user_album INTEGER NOT NULL DEFAULT 0,
    burst_key TEXT,
    burst_selected INTEGER NOT NULL DEFAULT 0,
    score_overall REAL,
    score_curation REAL,
    is_screenshot INTEGER NOT NULL DEFAULT 0,
    live_photo INTEGER NOT NULL DEFAULT 0,
    preview_path TEXT,
    last_seen_scan_id INTEGER
);

CREATE TABLE IF NOT EXISTS clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    item_count INTEGER NOT NULL,
    total_bytes INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cluster_members (
    cluster_id INTEGER NOT NULL REFERENCES clusters(id),
    asset_uuid TEXT NOT NULL REFERENCES assets(uuid),
    phash TEXT,
    rank INTEGER,
    proposal TEXT,                   -- 'keep' | 'archive' | 'unsure'
    PRIMARY KEY (cluster_id, asset_uuid)
);

CREATE TABLE IF NOT EXISTS decisions (
    asset_uuid TEXT PRIMARY KEY REFERENCES assets(uuid),
    decision TEXT NOT NULL,          -- 'keep' | 'archive'
    decided_at TEXT NOT NULL,
    cluster_id INTEGER
);

CREATE TABLE IF NOT EXISTS exports (
    asset_uuid TEXT PRIMARY KEY REFERENCES assets(uuid),
    export_paths TEXT NOT NULL,      -- JSON list
    bytes INTEGER,
    sha256_ok INTEGER NOT NULL DEFAULT 0,
    exported_at TEXT,
    in_staging_album INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    cutoff_date TEXT NOT NULL,
    counts TEXT                      -- JSON summary
);

CREATE INDEX IF NOT EXISTS idx_assets_date ON assets(date);
CREATE INDEX IF NOT EXISTS idx_cluster_members_cluster ON cluster_members(cluster_id);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
