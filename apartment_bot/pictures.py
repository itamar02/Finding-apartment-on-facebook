"""Download listing photos, only for posts that passed the filter.

The scanner stores photo links in `post_images`; this module downloads the files for
posts whose latest final decision (for any user) is in config `pictures.save_for`,
into <pictures.folder>/<post date>_<post id>/01.jpg, 02.jpg, ...

Facebook's photo links expire after some days, so download soon after analysis.
Photos that fail keep a `download_error` and are retried on the next run.
"""
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"


def qualifying_post_ids(db, save_for):
    """Posts whose latest successful analysis, for any user, has a final decision in save_for."""
    if not save_for:
        return []
    rows = db.execute(
        "SELECT DISTINCT a.post_id FROM post_analyses a"
        " WHERE a.status = 'ok' AND a.final_decision IN (%s)"
        " AND a.id = (SELECT max(b.id) FROM post_analyses b"
        "             WHERE b.post_id = a.post_id AND b.user_id = a.user_id AND b.status = 'ok')"
        % ",".join("?" * len(save_for)), list(save_for))
    return [r[0] for r in rows]


def post_folder(cfg, post):
    """Folder for one listing's photos, e.g. pictures/2026-10-03_3290449724484187."""
    date = (post["posted_at"] or "")[:10] or "undated"
    return PROJECT_DIR / cfg["pictures"]["folder"] / f"{date}_{post['post_id']}"


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        ctype = r.headers.get_content_type()
        if not ctype.startswith("image/"):
            raise ValueError(f"not an image ({ctype})")
        return r.read(), _EXT.get(ctype, ".jpg")


def download_post_pictures(db, cfg, post_id):
    """Download the not-yet-downloaded photos of one post. Returns (downloaded, failed)."""
    post = db.execute("SELECT post_id, posted_at FROM posts WHERE post_id = ?", (post_id,)).fetchone()
    pending = db.execute("SELECT idx, url FROM post_images WHERE post_id = ? AND local_path IS NULL"
                         " ORDER BY idx", (post_id,)).fetchall()
    if not post or not pending:
        return 0, 0
    folder = post_folder(cfg, post)
    folder.mkdir(parents=True, exist_ok=True)
    ok = failed = 0
    for idx, url in pending:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            data, ext = _fetch(url)
            path = folder / f"{idx:02d}{ext}"
            path.write_bytes(data)
            rel = path.relative_to(PROJECT_DIR).as_posix()
            with db:
                db.execute("UPDATE post_images SET local_path = ?, download_error = NULL, downloaded_at = ?"
                           " WHERE post_id = ? AND idx = ?", (rel, now, post_id, idx))
            ok += 1
        except (urllib.error.URLError, OSError, ValueError) as e:
            msg = f"HTTP {e.code} (link probably expired)" if isinstance(e, urllib.error.HTTPError) else str(e)
            with db:
                db.execute("UPDATE post_images SET download_error = ? WHERE post_id = ? AND idx = ?",
                           (msg, post_id, idx))
            failed += 1
    if not any(folder.iterdir()):
        folder.rmdir()
    return ok, failed


def download_pictures(db, cfg, post_ids=None):
    """Download photos for qualifying posts (optionally only among post_ids).

    Returns counts: posts with photos handled, photos downloaded, photos failed.
    """
    targets = qualifying_post_ids(db, cfg["pictures"]["save_for"])
    if post_ids is not None:
        wanted = set(post_ids)
        targets = [p for p in targets if p in wanted]
    stats = {"posts": 0, "downloaded": 0, "failed": 0}
    for pid in targets:
        ok, failed = download_post_pictures(db, cfg, pid)
        if ok or failed:
            stats["posts"] += 1
        stats["downloaded"] += ok
        stats["failed"] += failed
    return stats
