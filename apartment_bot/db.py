"""SQLite access and schema migrations.

The `posts` table (written by scan_facebook.py) is the immutable raw listing.
Analyses live in `post_analyses`; each attempt is a new row, so re-running with a
newer prompt/model never touches the original post or earlier analyses.
"""
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "apartments.db"

# Photo links seen in the feed (written by scan_facebook.py, which also creates the
# table). Files are downloaded later only for posts that pass the filter
# (pictures.py), so this table holds URLs, not images.
POST_IMAGES_DDL = """
CREATE TABLE IF NOT EXISTS post_images (
    post_id        TEXT NOT NULL REFERENCES posts(post_id),
    idx            INTEGER NOT NULL,            -- order in the post, from 1
    url            TEXT NOT NULL,               -- Facebook CDN link; expires after some days
    alt            TEXT,                        -- Facebook's automatic image description
    more_hidden    INTEGER NOT NULL DEFAULT 0,  -- photos behind a "+N" tile (same on every row)
    local_path     TEXT,                        -- set once downloaded, relative to the project
    download_error TEXT,
    downloaded_at  TEXT,
    PRIMARY KEY (post_id, idx)
);
"""

# Each migration moves the schema from version i to i+1 (PRAGMA user_version).
MIGRATIONS = [
    # 0 -> 1: AI analysis results, stored separately from the raw posts.
    """
    CREATE TABLE post_analyses (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id            TEXT NOT NULL REFERENCES posts(post_id),
        user_id            TEXT NOT NULL,
        pipeline_version   TEXT NOT NULL,   -- prompt + schema version
        config_hash        TEXT NOT NULL,   -- fingerprint of the user's config at analysis time
        model              TEXT NOT NULL,
        status             TEXT NOT NULL,   -- ok | error
        error              TEXT,
        ai_decision        TEXT,            -- REJECT | PASS | NEEDS_REVIEW
        ai_rule            TEXT,            -- config rule the AI blamed
        ai_reason          TEXT,
        final_decision     TEXT,            -- after hard-rule verification
        final_rule         TEXT,
        final_reason       TEXT,
        decided_by         TEXT,            -- ai | hard_rule
        facts_json         TEXT,            -- extracted facts
        hard_rules_json    TEXT,            -- code checks and their outcomes
        raw_response_json  TEXT,            -- full model output as returned
        input_tokens       INTEGER,
        output_tokens      INTEGER,
        cache_read_tokens  INTEGER,
        cache_write_tokens INTEGER,
        cost_usd           REAL,
        analyzed_at        TEXT NOT NULL
    );
    CREATE INDEX idx_analyses_lookup ON post_analyses(post_id, user_id, pipeline_version, status);
    CREATE INDEX idx_analyses_decision ON post_analyses(user_id, final_decision);
    """,
    # 1 -> 2: photo links (may already exist if the scanner ran first).
    POST_IMAGES_DDL,
    # 2 -> 3: listings the human rejected by hand (reject.py). Kept apart from the AI analyses.
    """
    CREATE TABLE human_decisions (
        post_id    TEXT NOT NULL REFERENCES posts(post_id),
        user_id    TEXT NOT NULL,
        decision   TEXT NOT NULL,        -- REJECT
        note       TEXT,
        decided_at TEXT NOT NULL,
        PRIMARY KEY (post_id, user_id)
    );
    """,
]


def connect(path=DB_PATH):
    """Open the database with dict-like rows and foreign keys enforced."""
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def migrate(path=DB_PATH):
    """Apply pending migrations, backing up the database file first.

    Returns (connection, list of backup paths). A no-op when already up to date.
    To change the schema, append a new SQL string to MIGRATIONS - never edit old ones.
    """
    db = connect(path)
    version = db.execute("PRAGMA user_version").fetchone()[0]
    pending = MIGRATIONS[version:]
    if not pending:
        return db, []
    db.close()
    backup = Path(path).with_name(f"{Path(path).stem}.backup-v{version}-{datetime.now():%Y%m%d-%H%M%S}.db")
    shutil.copy2(path, backup)
    db = connect(path)
    for i, sql in enumerate(pending, start=version):
        try:
            db.executescript("BEGIN;" + sql + f"; PRAGMA user_version = {i + 1}; COMMIT;")
        except Exception:
            db.rollback()
            raise
    return db, [str(backup)]
