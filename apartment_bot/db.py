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
    # 3 -> 4: the phone app. "My status" is a pipeline separate from the AI verdict (which
    # stays derived from post_analyses). Statuses are a table so the app can edit them.
    # Existing hand rejections become "not relevant" (source human); human_decisions stays
    # and is kept in sync by statuses.py, so reject.py / why.py / exports keep working.
    """
    CREATE TABLE statuses (
        key        TEXT PRIMARY KEY,           -- stable id; the label can be renamed
        label      TEXT NOT NULL,
        color      TEXT NOT NULL,
        sort_order INTEGER NOT NULL,
        needs_note INTEGER NOT NULL DEFAULT 0, -- the app asks for a note (e.g. what to ask)
        built_in   INTEGER NOT NULL DEFAULT 0  -- built-in keys have meaning in code; can't be deleted
    );
    INSERT INTO statuses (key, label, color, sort_order, needs_note, built_in) VALUES
        ('new',               'חדש',          '#64748b', 10,  0, 1),
        ('interested',        'מעניין',        '#0ea5e9', 20,  0, 1),
        ('needs_details',     'צריך פרטים',    '#a855f7', 30,  1, 1),
        ('contacted',         'פניתי',         '#f59e0b', 40,  0, 1),
        ('awaiting_reply',    'מחכה לתשובה',   '#eab308', 50,  0, 1),
        ('meeting_scheduled', 'נקבעה פגישה',   '#22c55e', 60,  0, 1),
        ('visited',           'ביקרתי',        '#14b8a6', 70,  0, 1),
        ('top_candidate',     'מועמד מוביל',   '#ec4899', 80,  0, 1),
        ('not_relevant',      'לא רלוונטי',    '#6b7280', 90,  0, 1),
        ('taken',             'נתפס',          '#374151', 100, 0, 1);
    CREATE TABLE post_status (
        post_id    TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        status     TEXT NOT NULL REFERENCES statuses(key),
        note       TEXT,
        source     TEXT NOT NULL CHECK (source IN ('human', 'gpt', 'system')),
        updated_at TEXT NOT NULL,
        PRIMARY KEY (post_id, user_id)
    );
    CREATE INDEX idx_post_status_status ON post_status(user_id, status);
    CREATE TABLE post_status_history (     -- append-only; status NULL = status cleared
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id         TEXT NOT NULL,
        user_id         TEXT NOT NULL,
        status          TEXT,
        note            TEXT,
        source          TEXT NOT NULL,
        previous_status TEXT,
        previous_note   TEXT,
        changed_at      TEXT NOT NULL
    );
    CREATE INDEX idx_status_history_post ON post_status_history(post_id, user_id, id);
    CREATE TABLE post_notes (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id    TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        body       TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX idx_post_notes_post ON post_notes(post_id, user_id);
    CREATE TABLE post_seen (               -- opened in the app (used for "post edited" alerts)
        post_id        TEXT NOT NULL,
        user_id        TEXT NOT NULL,
        first_seen_at  TEXT NOT NULL,
        last_seen_at   TEXT NOT NULL,
        PRIMARY KEY (post_id, user_id)
    );
    CREATE TABLE app_settings (
        key        TEXT PRIMARY KEY,
        value_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    INSERT INTO post_status (post_id, user_id, status, note, source, updated_at)
        SELECT post_id, user_id, 'not_relevant', note, 'human', decided_at
        FROM human_decisions WHERE decision = 'REJECT';
    INSERT INTO post_status_history (post_id, user_id, status, note, source, changed_at)
        SELECT post_id, user_id, 'not_relevant', note, 'human', decided_at
        FROM human_decisions WHERE decision = 'REJECT';
    """,
    # 4 -> 5: Facebook groups move from config.yaml into the database (config.yaml's list
    # seeds it once, see groups.py), plus a log of statuses imported from an external AI.
    """
    CREATE TABLE fb_groups (
        id              TEXT PRIMARY KEY,      -- number or name from facebook.com/groups/<id>/
        name            TEXT,
        url             TEXT,
        member_count    INTEGER,
        privacy         TEXT,                  -- public | private | NULL (unknown)
        is_member       INTEGER,               -- 1 / 0 / NULL (unknown)
        description     TEXT,
        relevance_score REAL,                  -- 0-10 from the optional AI rating
        relevance_reason TEXT,
        selected        INTEGER NOT NULL DEFAULT 0,  -- scanned by scan_facebook.py
        source          TEXT NOT NULL,         -- config | my_groups | search | manual
        search_query    TEXT,                  -- the search that found it
        discovered_at   TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    );
    CREATE TABLE gpt_imports (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        items_json TEXT NOT NULL,              -- what was applied: [{post_id, status, note, previous}]
        applied    INTEGER NOT NULL
    );
    """,
    # 5 -> 6: background work. Edited posts get new versions here (posts stays as first
    # seen); repeated ads are grouped into listings with a price history; notifications,
    # follow-up reminders, push subscriptions and AI calls (for rate limits) are logged.
    """
    CREATE TABLE post_versions (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id        TEXT NOT NULL,
        text           TEXT NOT NULL,
        text_truncated INTEGER NOT NULL DEFAULT 0,
        kind           TEXT NOT NULL,          -- edit | expanded (a cut text later read in full)
        seen_at        TEXT NOT NULL
    );
    CREATE INDEX idx_post_versions_post ON post_versions(post_id, id);
    CREATE TABLE listing_posts (               -- the same apartment posted more than once
        post_id    TEXT PRIMARY KEY,
        listing_id TEXT NOT NULL,              -- the first post of the group
        matched_by TEXT NOT NULL,              -- self | text | phone | author_text
        rent_ils   INTEGER,
        posted_at  TEXT
    );
    CREATE INDEX idx_listing_posts_listing ON listing_posts(listing_id);
    CREATE TABLE notifications (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    TEXT NOT NULL,
        kind       TEXT NOT NULL,              -- new_listing | post_edited | followup | price_drop
        post_id    TEXT,
        dedupe_key TEXT NOT NULL,              -- one notification per event
        title      TEXT NOT NULL,
        body       TEXT NOT NULL,
        created_at TEXT NOT NULL,
        sent_at    TEXT,                       -- NULL while waiting (quiet hours) or failing
        channels   TEXT,                       -- where it went
        error      TEXT,
        UNIQUE (user_id, dedupe_key)
    );
    CREATE TABLE followups (                   -- snooze / dismiss per post and status period
        post_id        TEXT NOT NULL,
        user_id        TEXT NOT NULL,
        status_since   TEXT NOT NULL,          -- post_status updated_at|status this applies to
        snoozed_until  TEXT,
        dismissed      INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (post_id, user_id, status_since)
    );
    CREATE TABLE push_subscriptions (
        endpoint   TEXT PRIMARY KEY,
        user_id    TEXT NOT NULL,
        keys_json  TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE ai_calls (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        provider  TEXT NOT NULL,
        called_at REAL NOT NULL                -- unix time
    );
    CREATE INDEX idx_ai_calls ON ai_calls(provider, called_at);
    """,
]

# A post's current text: its latest version if it was edited, else the text first seen.
EFFECTIVE_TEXT_SQL = ("coalesce((SELECT v.text FROM post_versions v WHERE v.post_id = p.post_id"
                      " ORDER BY v.id DESC LIMIT 1), p.text)")


def connect(path=DB_PATH, check_same_thread=True):
    """Open the database with dict-like rows and foreign keys enforced.

    The scanner, the analysis and the app server can write at the same time, so a writer
    waits up to 30 seconds for another one's transaction instead of failing at once.
    """
    db = sqlite3.connect(path, timeout=30, check_same_thread=check_same_thread)
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
