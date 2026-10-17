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
    # 6 -> 7: visits (checklist, notes, my photos, rating) and meetings proposed from a
    # WhatsApp chat I shared, which become calendar events only after I confirm.
    """
    CREATE TABLE visit_items (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        label      TEXT NOT NULL,
        sort_order INTEGER NOT NULL,
        active     INTEGER NOT NULL DEFAULT 1   -- removed items stay for old visits
    );
    INSERT INTO visit_items (label, sort_order) VALUES
        ('לחץ מים', 10), ('עובש / רטיבות', 20), ('רעש', 30), ('מקום אחסון', 40), ('חניה', 50),
        ('חשבונות (חשמל, מים, ארנונה, ועד)', 60), ('תחושה מבעל הדירה', 70);
    CREATE TABLE visits (
        post_id    TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        rating     INTEGER CHECK (rating BETWEEN 1 AND 5),
        notes      TEXT,
        visited_at TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (post_id, user_id)
    );
    CREATE TABLE visit_checks (
        post_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        item_id INTEGER NOT NULL REFERENCES visit_items(id),
        state   TEXT CHECK (state IN ('ok', 'issue', 'na')),
        note    TEXT,
        PRIMARY KEY (post_id, user_id, item_id)
    );
    CREATE TABLE visit_photos (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id    TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        path       TEXT NOT NULL,              -- relative to the project, under uploads/
        created_at TEXT NOT NULL
    );
    CREATE TABLE chats (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    TEXT NOT NULL,
        post_id    TEXT,                       -- linked listing (by phone, or picked by hand)
        source     TEXT NOT NULL,              -- paste | upload | share
        title      TEXT,
        text       TEXT NOT NULL,
        phones     TEXT,                       -- JSON list found in the chat
        created_at TEXT NOT NULL
    );
    CREATE TABLE meeting_proposals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id     INTEGER NOT NULL REFERENCES chats(id),
        post_id     TEXT,
        found       INTEGER NOT NULL,          -- 0: no agreed time in the chat
        date        TEXT,                      -- YYYY-MM-DD
        time        TEXT,                      -- HH:MM
        address     TEXT,
        evidence    TEXT,                      -- JSON list of quoted chat lines
        status      TEXT NOT NULL,             -- proposed | confirmed | discarded
        event_id    TEXT,
        event_link  TEXT,
        model       TEXT,
        created_at  TEXT NOT NULL
    );
    """,
    # 7 -> 8: config.yaml changes suggested from my own statuses and notes (learning loop).
    """
    CREATE TABLE config_proposals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     TEXT NOT NULL,
        path        TEXT NOT NULL,             -- e.g. features.dealbreakers
        op          TEXT NOT NULL,             -- add | remove | set
        value_json  TEXT NOT NULL,
        reason      TEXT NOT NULL,
        evidence    TEXT NOT NULL,             -- JSON list of post ids that support it
        status      TEXT NOT NULL,             -- open | accepted | declined
        created_at  TEXT NOT NULL,
        decided_at  TEXT
    );
    """,
    # 8 -> 9: the location of a listing as I corrected it (shown instead of the AI's reading).
    """
    CREATE TABLE post_location (
        post_id    TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        location   TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (post_id, user_id)
    );
    """,
    # 9 -> 10: the monthly rent as I corrected it (the post's price changed, or the AI misread it).
    """
    CREATE TABLE post_price (
        post_id    TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        rent_ils   INTEGER NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (post_id, user_id)
    );
    """,
    # 10 -> 11: whether the rent includes the bills (arnona, vaad, water, electricity...), as I set it.
    """
    CREATE TABLE post_bills (
        post_id    TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        bills      TEXT NOT NULL CHECK (bills IN ('included', 'excluded')),
        updated_at TEXT NOT NULL,
        PRIMARY KEY (post_id, user_id)
    );
    """,
    # 11 -> 12: a status for "not thrilled, but not in the trash": a fallback option.
    """
    INSERT OR IGNORE INTO statuses (key, label, color, sort_order, needs_note, built_in)
        VALUES ('backup', 'גיבוי', '#fb923c', 25, 0, 1);
    """,
    # 12 -> 13: when the viewing is (shown on the listing), set by hand or from the calendar flows.
    """
    CREATE TABLE post_meeting (
        post_id    TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        meeting_at TEXT NOT NULL,          -- local time, YYYY-MM-DDTHH:MM
        address    TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (post_id, user_id)
    );
    """,
    # 13 -> 14: a free-text note on the price (what is and isn't included, extra costs...).
    """
    CREATE TABLE post_price_note (
        post_id    TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        note       TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (post_id, user_id)
    );
    """,
    # 14 -> 15: the stronger model's estimate of the trip to the configured destination (approved listings only).
    """
    CREATE TABLE post_commute (
        post_id       TEXT NOT NULL,
        user_id       TEXT NOT NULL,
        destination   TEXT NOT NULL,
        from_place    TEXT,                -- the place the estimate started from
        minutes       INTEGER,             -- door to door, null when it could not be estimated
        within_limit  INTEGER,             -- 1 / 0 against commute.absolute_max_minutes, null if unknown
        route         TEXT,
        uses_scooter  INTEGER NOT NULL DEFAULT 0,
        scooter_minutes INTEGER,
        confidence    TEXT,                -- low | medium | high
        model         TEXT,
        cost_usd      REAL,
        checked_at    TEXT NOT NULL,
        PRIMARY KEY (post_id, user_id)
    );
    """,
    # 15 -> 16: the nearest station / stop on the trip and the walk to it (known when the post names a street).
    """
    ALTER TABLE post_commute ADD COLUMN station TEXT;
    ALTER TABLE post_commute ADD COLUMN walk_minutes INTEGER;
    """,
    # 16 -> 17: one estimate per destination (several destinations are allowed).
    """
    CREATE TABLE post_commute_new (
        post_id       TEXT NOT NULL,
        user_id       TEXT NOT NULL,
        destination   TEXT NOT NULL,
        from_place    TEXT,
        minutes       INTEGER,
        within_limit  INTEGER,
        route         TEXT,
        uses_scooter  INTEGER NOT NULL DEFAULT 0,
        scooter_minutes INTEGER,
        confidence    TEXT,
        model         TEXT,
        cost_usd      REAL,
        checked_at    TEXT NOT NULL,
        station       TEXT,
        walk_minutes  INTEGER,
        arrive_by     TEXT,                -- the arrival time the estimate was made for, e.g. 08:30
        days          TEXT,
        PRIMARY KEY (post_id, user_id, destination)
    );
    INSERT INTO post_commute_new (post_id, user_id, destination, from_place, minutes, within_limit, route, uses_scooter,
                                  scooter_minutes, confidence, model, cost_usd, checked_at, station, walk_minutes)
        SELECT post_id, user_id, destination, from_place, minutes, within_limit, route, uses_scooter,
               scooter_minutes, confidence, model, cost_usd, checked_at, station, walk_minutes FROM post_commute;
    DROP TABLE post_commute;
    ALTER TABLE post_commute_new RENAME TO post_commute;
    """,
    # 17 -> 18: addresses already turned into coordinates (Nominatim is polite-use only, so never ask twice).
    """
    CREATE TABLE geocode_cache (
        query     TEXT PRIMARY KEY,
        lat       REAL,
        lon       REAL,
        precision TEXT,                    -- street | area
        name      TEXT,
        cached_at TEXT NOT NULL
    );
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
