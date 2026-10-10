"""Notifications: Web Push to the installed app, or a Telegram bot, or both.

Events (each announced once, see `notifications.dedupe_key`):
  new_listing  a newly analyzed listing whose AI decision is one I chose, under my price ceiling
  post_edited  a post I had seen (opened, or gave a status) was edited
  followup     a post waited too long in Contacted / Awaiting reply / Needs details
  price_drop   the same apartment was posted again for less
During quiet hours notifications are stored and sent when they end.

Private files (git-ignored): secrets/vapid.json (Web Push keys) and secrets/telegram.json
({"token", "chat_id"}). Without telegram.json the watchdog's bot files are used if present.
"""
import base64
import json
import urllib.parse
import urllib.request
from datetime import datetime, time as dtime
from pathlib import Path

from . import followups, groups as groups_mod, prices, settings, versions
from .statuses import now_iso

PROJECT_DIR = Path(__file__).resolve().parent.parent
SECRETS_DIR = PROJECT_DIR / "secrets"
VAPID_FILE = SECRETS_DIR / "vapid.json"
TELEGRAM_FILE = SECRETS_DIR / "telegram.json"
WATCHDOG_STATE = PROJECT_DIR / "watchdog" / "state"
VAPID_CLAIMS = {"sub": "mailto:apartmentbot@users.noreply.github.com"}


# --- keys and channels --------------------------------------------------------------------

def vapid_keys():
    """{"private_pem", "public_key"} for Web Push, created on first use."""
    if not VAPID_FILE.exists():
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid01
        v = Vapid01()
        v.generate_keys()
        raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        pem = v.private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()).decode()
        SECRETS_DIR.mkdir(exist_ok=True)
        VAPID_FILE.write_text(json.dumps({"private_pem": pem,
                                          "public_key": base64.urlsafe_b64encode(raw).rstrip(b"=").decode()}),
                              encoding="utf-8")
    return json.loads(VAPID_FILE.read_text(encoding="utf-8"))


def telegram_config():
    """{"token", "chat_id"} or None."""
    try:
        conf = json.loads(TELEGRAM_FILE.read_text(encoding="utf-8"))
        if conf.get("token") and conf.get("chat_id"):
            return conf
    except (OSError, ValueError):
        pass
    try:
        token = (WATCHDOG_STATE / "telegram_bot_token.txt").read_text(encoding="utf-8").strip()
        chat = (WATCHDOG_STATE / "telegram_chat_id.txt").read_text(encoding="utf-8").strip()
        if token and chat:
            return {"token": token, "chat_id": chat, "from_watchdog": True}
    except OSError:
        pass
    return None


def save_telegram(token, chat_id):
    SECRETS_DIR.mkdir(exist_ok=True)
    TELEGRAM_FILE.write_text(json.dumps({"token": token.strip(), "chat_id": str(chat_id).strip()}), encoding="utf-8")


def add_subscription(db, user_id, sub):
    with db:
        db.execute("INSERT OR REPLACE INTO push_subscriptions (endpoint, user_id, keys_json, created_at)"
                   " VALUES (?,?,?,?)", (sub["endpoint"], user_id, json.dumps(sub.get("keys") or {}), now_iso()))


def remove_subscription(db, endpoint):
    with db:
        db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))


def _send_push(db, user_id, n, sender=None):
    subs = db.execute("SELECT endpoint, keys_json FROM push_subscriptions WHERE user_id = ?", (user_id,)).fetchall()
    if not subs:
        raise RuntimeError("no phone has turned on notifications")
    if sender is None:
        from pywebpush import webpush, WebPushException
        keys = vapid_keys()

        def sender(sub, payload):
            try:
                webpush(sub, payload, vapid_private_key=keys["private_pem"], vapid_claims=dict(VAPID_CLAIMS), ttl=86400)
            except WebPushException as e:
                gone = getattr(e, "response", None) is not None and e.response.status_code in (404, 410)
                if gone:
                    remove_subscription(db, sub["endpoint"])
                raise RuntimeError(f"push failed: {e}")
    payload = json.dumps({"title": n["title"], "body": n["body"],
                          "url": f"/#post={n['post_id']}" if n["post_id"] else "/", "tag": n["dedupe_key"]},
                         ensure_ascii=False)
    errors = []
    for endpoint, keys_json in subs:
        try:
            sender({"endpoint": endpoint, "keys": json.loads(keys_json)}, payload)
        except RuntimeError as e:
            errors.append(str(e))
    if len(errors) == len(subs):
        raise RuntimeError("; ".join(dict.fromkeys(errors)))


def _send_telegram(db, n, opener=urllib.request.urlopen):
    conf = telegram_config()
    if not conf:
        raise RuntimeError("Telegram is not set up")
    lines = [n["title"], n["body"]]
    if n["post_id"]:
        url = db.execute("SELECT url FROM posts WHERE post_id = ?", (n["post_id"],)).fetchone()
        app_url = settings.get(db, "app_url")
        if app_url:
            lines.append(f"{app_url.rstrip('/')}/#post={n['post_id']}")
        if url:
            lines.append(url[0])
    data = urllib.parse.urlencode({"chat_id": conf["chat_id"], "text": "\n".join(lines),
                                   "disable_web_page_preview": "true"}).encode()
    with opener(f"https://api.telegram.org/bot{conf['token']}/sendMessage", data=data, timeout=30) as r:
        if not json.loads(r.read().decode()).get("ok"):
            raise RuntimeError("Telegram refused the message")


# --- queue and delivery --------------------------------------------------------------------

def in_quiet_hours(quiet, now=None):
    """True when `now` is inside {"start": "23:00", "end": "07:00"} (may cross midnight)."""
    if not quiet or not quiet.get("start") or not quiet.get("end"):
        return False
    now = (now or datetime.now().astimezone()).time()
    start, end = dtime.fromisoformat(quiet["start"]), dtime.fromisoformat(quiet["end"])
    return start <= now < end if start < end else (now >= start or now < end)


def queue(db, user_id, kind, dedupe_key, title, body, post_id=None):
    """Store a notification once. Returns True if it is new."""
    with db:
        cur = db.execute("INSERT OR IGNORE INTO notifications (user_id, kind, post_id, dedupe_key, title, body,"
                         " created_at) VALUES (?,?,?,?,?,?,?)",
                         (user_id, kind, post_id, dedupe_key, title, body, now_iso()))
    return cur.rowcount == 1


def deliver_pending(db, user_id, now=None, push_sender=None, telegram_opener=None):
    """Send what is waiting, unless notifications are off or it's quiet hours. Returns count sent."""
    conf = settings.get(db, "notifications")
    if not conf.get("enabled") or in_quiet_hours(conf.get("quiet_hours"), now):
        return 0
    channel = conf.get("channel", "push")
    pending = db.execute("SELECT * FROM notifications WHERE user_id = ? AND sent_at IS NULL"
                         " AND created_at > ? ORDER BY id", (user_id, _days_ago(3))).fetchall()
    sent = 0
    for n in pending:
        used, errors = [], []
        for ch in (["push", "telegram"] if channel == "both" else [channel]):
            try:
                if ch == "push":
                    _send_push(db, user_id, n, push_sender)
                else:
                    _send_telegram(db, n, **({"opener": telegram_opener} if telegram_opener else {}))
                used.append(ch)
            except Exception as e:   # one channel failing must not block the other or the queue
                errors.append(f"{ch}: {e}")
        with db:
            db.execute("UPDATE notifications SET sent_at = ?, channels = ?, error = ? WHERE id = ?",
                       (now_iso() if used else None, ",".join(used) or None, "; ".join(errors) or None, n["id"]))
        sent += bool(used)
    return sent


def _days_ago(days):
    from datetime import timedelta
    return (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")


# --- events ------------------------------------------------------------------------------

def _money(n):
    return f"{n:,} ₪" if n else "מחיר לא צוין"


def _place(facts):
    return facts.get("neighborhood") or facts.get("city") or facts.get("location_summary_en") or ""


def collect_events(db, cfg, user_id, since_iso, now=None):
    """Queue every event since `since_iso` that the settings ask for. Returns how many were new."""
    conf = settings.get(db, "notifications")
    names = groups_mod.names(db, cfg)
    new = 0

    # New listings: the latest analysis of each post analyzed since then.
    decisions = conf.get("decisions") or []
    if decisions:
        rows = db.execute(
            "SELECT a.post_id, a.final_decision, a.final_reason, a.facts_json, p.group_id FROM post_analyses a"
            " JOIN posts p ON p.post_id = a.post_id"
            " WHERE a.user_id = ? AND a.status = 'ok' AND a.analyzed_at >= ?"
            " AND a.id = (SELECT max(b.id) FROM post_analyses b WHERE b.post_id = a.post_id"
            "             AND b.user_id = a.user_id AND b.status = 'ok')"
            f" AND a.final_decision IN ({','.join('?' * len(decisions))})"
            " AND NOT EXISTS (SELECT 1 FROM post_status s WHERE s.post_id = a.post_id AND s.user_id = a.user_id)",
            (user_id, since_iso, *decisions)).fetchall()
        ceiling = conf.get("price_ceiling")
        for r in rows:
            facts = json.loads(r["facts_json"]) if r["facts_json"] else {}
            rent = facts.get("rent_ils")
            if ceiling and rent and rent > ceiling:
                continue
            title = ("דירה מתאימה" if r["final_decision"] == "PASS" else "דירה לבדיקה") + \
                f": {_money(rent)}" + (f" · {_place(facts)}" if _place(facts) else "")
            new += queue(db, user_id, "new_listing", f"new:{r['post_id']}", title,
                         f"{names.get(r['group_id'], r['group_id'])} · {r['final_reason'] or ''}"[:200], r["post_id"])

    # Edits to posts I had seen before the edit.
    if conf.get("edits", True):
        rows = db.execute(
            "SELECT v.id, v.post_id, v.text FROM post_versions v JOIN post_seen s ON s.post_id = v.post_id"
            " AND s.user_id = ? WHERE v.kind = 'edit' AND v.seen_at >= ? AND s.first_seen_at < v.seen_at",
            (user_id, since_iso)).fetchall()
        for r in rows:
            summary = versions.change_summary(versions.previous_text(db, r["id"]), r["text"])
            new += queue(db, user_id, "post_edited", f"edit:{r['id']}", "מודעה שראית עודכנה", summary, r["post_id"])

    # Follow-ups that are due (once per status period and snooze).
    if conf.get("followups", True):
        labels = {k: v for k, v in db.execute("SELECT key, label FROM statuses")}
        for f in followups.due(db, user_id, now):
            new += queue(db, user_id, "followup", f"followup:{f['post_id']}:{f['status']}:{f['since']}:{f['snoozed_until'] or ''}",
                         f"מעקב: {labels.get(f['status'], f['status'])} כבר {round(f['overdue_hours'])} שעות מעבר לזמן",
                         f["note"] or "כדאי לבדוק אם יש תשובה", f["post_id"])
    return new


def queue_price_drops(db, user_id, drops):
    """Queue price-drop notifications from prices.update()."""
    conf = settings.get(db, "notifications")
    if not conf.get("price_drop", True):
        return 0
    new = 0
    for d in drops:
        new += queue(db, user_id, "price_drop", f"drop:{d['listing_id']}:{d['post_id']}",
                     f"ירידת מחיר: {_money(d['old_rent'])} ← {_money(d['new_rent'])}",
                     "אותה דירה פורסמה שוב במחיר נמוך יותר", d["post_id"])
    return new


def run_price_tracking(db, user_id, notify=True):
    """Group new posts into listings; queue drops (skipped on the first, backfilling run)."""
    first_run = not db.execute("SELECT 1 FROM listing_posts LIMIT 1").fetchone()
    conf = settings.get(db, "notifications")
    drops = prices.update(db, user_id, conf.get("price_drop_min") or 0)
    return queue_price_drops(db, user_id, drops) if notify and not first_run else 0
