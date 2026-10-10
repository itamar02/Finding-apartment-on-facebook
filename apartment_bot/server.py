"""The phone app's server: JSON API + the built web app (web/dist). Started by app_server.py.

Every /api route except /api/pair needs the app token (cookie or "Authorization: Bearer").
Changes also need the header "X-ApartmentBot: 1", which a page from another site cannot
send without the browser asking first, so a link elsewhere can't change anything here.
"""
import asyncio
from contextlib import asynccontextmanager
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import (auth, db as dbmod, feed, followups, gpt_io, groups as groups_mod, notify, prices, providers,
               settings, statuses)
from .ratelimit import RateLimiter
from .scheduler import Scheduler
from .config import get_user

PROJECT_DIR = Path(__file__).resolve().parent.parent
WEB_DIST = PROJECT_DIR / "web" / "dist"
PICTURES_DIR = PROJECT_DIR / "pictures"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class PairIn(BaseModel):
    code: str


class StatusIn(BaseModel):
    status: Optional[str] = None     # None clears the status
    note: Optional[str] = None


class NoteIn(BaseModel):
    body: str


class StatusDefIn(BaseModel):
    key: Optional[str] = None
    label: str
    color: str = "#64748b"
    needs_note: bool = False


class OrderIn(BaseModel):
    keys: list[str]


class GroupIn(BaseModel):
    selected: bool


class NewGroupIn(BaseModel):
    url: str
    name: Optional[str] = None


class DiscoverIn(BaseModel):
    score: bool = False


class SnoozeIn(BaseModel):
    hours: float = 24


class PushSubIn(BaseModel):
    endpoint: str
    keys: dict = {}


class TelegramIn(BaseModel):
    token: str
    chat_id: str


class AiIn(BaseModel):
    ai_providers: list[dict]
    ai_stages: dict
    anthropic_limits: dict = {}


class KeyIn(BaseModel):
    key: str = ""


class ReplyIn(BaseModel):
    text: str
    override_human: bool = False


def create_app(cfg, user_id=None, db_path=dbmod.DB_PATH, web_dist=WEB_DIST, secure_cookie=True,
               run_scheduler=True):
    user = get_user(cfg, user_id)
    uid = user["id"]
    scheduler = Scheduler(cfg, uid, db_path)

    @asynccontextmanager
    async def lifespan(_app):
        if run_scheduler:
            scheduler.start()
        yield
        scheduler.stop()

    app = FastAPI(title="ApartmentBot", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.scheduler = scheduler

    def get_db():
        db = dbmod.connect(db_path, check_same_thread=False)
        try:
            yield db
        finally:
            db.close()

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path != "/api/pair":
            bearer = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not auth.token_ok(request.cookies.get(auth.COOKIE) or bearer):
                return JSONResponse({"detail": "not paired"}, status_code=401)
            if request.method in WRITE_METHODS and request.headers.get("x-apartmentbot") != "1":
                return JSONResponse({"detail": "missing X-ApartmentBot header"}, status_code=403)
        return await call_next(request)

    # --- login ------------------------------------------------------------------------------

    @app.post("/api/pair")
    async def pair(body: PairIn, response: Response):
        token = auth.redeem_pairing_code(body.code)
        if not token:
            await asyncio.sleep(1)   # slows down guessing on top of the try limit
            raise HTTPException(403, "wrong or expired code")
        response.set_cookie(auth.COOKIE, token, max_age=400 * 86400, httponly=True, secure=secure_cookie,
                            samesite="strict")
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(auth.COOKIE)
        return {"ok": True}

    # --- reference data -------------------------------------------------------------------------

    @app.get("/api/meta")
    def meta(db=Depends(get_db)):
        return {
            "user_id": uid,
            "statuses": statuses.list_statuses(db),
            "groups": [{"id": k, "name": v} for k, v in feed.group_names(db, cfg).items()],
            "decisions": list(feed.DECISIONS),
            "settings": settings.get_all(db),
            "recent_days": settings.recent_days(db, cfg),
        }

    # --- posts ----------------------------------------------------------------------------------

    @app.get("/api/posts")
    def posts(db=Depends(get_db),
              decision: list[str] = Query(default=[]), status: list[str] = Query(default=[]),
              group: list[str] = Query(default=[]), price_min: Optional[int] = None,
              price_max: Optional[int] = None, area: Optional[str] = None, since: Optional[str] = None,
              until: Optional[str] = None, include_old: bool = False, has_phone: Optional[bool] = None,
              source: Optional[str] = None, sort: str = "newest", offset: int = 0,
              limit: int = Query(default=30, le=500)):
        if sort not in feed.SORTS:
            raise HTTPException(400, f"sort must be one of {', '.join(feed.SORTS)}")
        return feed.query_posts(db, cfg, uid, decisions=decision or None, statuses_=status or None,
                                groups=group or None, price_min=price_min, price_max=price_max, area=area,
                                since=since, until=until, include_old=include_old, has_phone=has_phone,
                                status_source=source, sort=sort, offset=offset, limit=limit)

    @app.get("/api/posts/{post_id}")
    def post(post_id: str, db=Depends(get_db)):
        card = feed.post_detail(db, cfg, uid, post_id)
        if not card:
            raise HTTPException(404, "unknown post")
        card["price_history"] = prices.history(db, post_id)
        card["versions"] = [dict(v) for v in db.execute(
            "SELECT id, kind, seen_at, text FROM post_versions WHERE post_id = ? ORDER BY id", (post_id,))]
        return card

    def _status_result(db, post_id, old):
        return {"previous": old, "status": statuses.current(db, uid, post_id)}

    @app.put("/api/posts/{post_id}/status")
    def put_status(post_id: str, body: StatusIn, db=Depends(get_db)):
        try:
            old, _ = statuses.set_status(db, uid, post_id, body.status, body.note, "human")
        except statuses.StatusError as e:
            raise HTTPException(400, str(e))
        return _status_result(db, post_id, old)

    @app.post("/api/posts/{post_id}/status/undo")
    def undo_status(post_id: str, db=Depends(get_db)):
        try:
            statuses.undo(db, uid, post_id)
        except statuses.StatusError as e:
            raise HTTPException(400, str(e))
        return {"status": statuses.current(db, uid, post_id)}

    @app.post("/api/posts/{post_id}/contacted")
    def contacted(post_id: str, db=Depends(get_db)):
        """Called when the WhatsApp / contact button is tapped; the app offers Undo."""
        try:
            old, _ = statuses.set_status(db, uid, post_id, "contacted", None, "human")
        except statuses.StatusError as e:
            raise HTTPException(400, str(e))
        return _status_result(db, post_id, old)

    # --- notes ----------------------------------------------------------------------------------

    @app.post("/api/posts/{post_id}/notes")
    def add_note(post_id: str, body: NoteIn, db=Depends(get_db)):
        if not body.body.strip():
            raise HTTPException(400, "empty note")
        if not db.execute("SELECT 1 FROM posts WHERE post_id = ?", (post_id,)).fetchone():
            raise HTTPException(404, "unknown post")
        now = statuses.now_iso()
        with db:
            cur = db.execute("INSERT INTO post_notes (post_id, user_id, body, created_at, updated_at)"
                             " VALUES (?,?,?,?,?)", (post_id, uid, body.body.strip(), now, now))
        return {"id": cur.lastrowid, "body": body.body.strip(), "created_at": now, "updated_at": now}

    @app.put("/api/notes/{note_id}")
    def edit_note(note_id: int, body: NoteIn, db=Depends(get_db)):
        now = statuses.now_iso()
        with db:
            n = db.execute("UPDATE post_notes SET body = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                           (body.body.strip(), now, note_id, uid)).rowcount
        if not n:
            raise HTTPException(404, "unknown note")
        return {"ok": True}

    @app.delete("/api/notes/{note_id}")
    def delete_note(note_id: int, db=Depends(get_db)):
        with db:
            n = db.execute("DELETE FROM post_notes WHERE id = ? AND user_id = ?", (note_id, uid)).rowcount
        if not n:
            raise HTTPException(404, "unknown note")
        return {"ok": True}

    # --- photos ---------------------------------------------------------------------------------

    @app.get("/api/photos/{post_id}/{idx}")
    def photo(post_id: str, idx: int, db=Depends(get_db)):
        row = db.execute("SELECT local_path FROM post_images WHERE post_id = ? AND idx = ?", (post_id, idx)).fetchone()
        if not row or not row["local_path"]:
            raise HTTPException(404, "photo not downloaded")
        path = (PROJECT_DIR / row["local_path"]).resolve()
        if PICTURES_DIR.resolve() not in path.parents or not path.is_file():
            raise HTTPException(404, "photo missing")
        return FileResponse(path, headers={"Cache-Control": "private, max-age=604800"})

    # --- settings and the status list -----------------------------------------------------------

    @app.get("/api/settings")
    def get_settings(db=Depends(get_db)):
        return settings.get_all(db)

    @app.put("/api/settings")
    def put_settings(values: dict, db=Depends(get_db)):
        try:
            return settings.put(db, values)
        except KeyError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/statuses")
    def add_status(body: StatusDefIn, db=Depends(get_db)):
        try:
            key = body.key or f"custom_{secrets.token_hex(3)}"   # labels are Hebrew; keys stay ASCII
            if key in statuses.status_keys(db):
                raise statuses.StatusError(f"status {key!r} already exists")
            key = statuses.save_status(db, key, body.label, body.color, body.needs_note)
        except statuses.StatusError as e:
            raise HTTPException(400, str(e))
        return {"key": key, "statuses": statuses.list_statuses(db)}

    @app.put("/api/statuses/order")
    def order_statuses(body: OrderIn, db=Depends(get_db)):
        try:
            statuses.reorder(db, body.keys)
        except statuses.StatusError as e:
            raise HTTPException(400, str(e))
        return statuses.list_statuses(db)

    @app.put("/api/statuses/{key}")
    def edit_status(key: str, body: StatusDefIn, db=Depends(get_db)):
        if key not in statuses.status_keys(db):
            raise HTTPException(404, "unknown status")
        statuses.save_status(db, key, body.label, body.color, body.needs_note)
        return statuses.list_statuses(db)

    @app.delete("/api/statuses/{key}")
    def remove_status(key: str, db=Depends(get_db)):
        try:
            statuses.delete_status(db, key)
        except statuses.StatusError as e:
            raise HTTPException(400, str(e))
        return statuses.list_statuses(db)

    # --- Facebook groups ------------------------------------------------------------------------

    discovery = {"proc": None}
    discover_log = PROJECT_DIR / "discover_groups.log"

    @app.get("/api/groups")
    def list_groups(db=Depends(get_db)):
        return {"groups": groups_mod.list_groups(db), "queries": settings.group_queries(db, user)}

    @app.put("/api/groups/{gid}")
    def select_group(gid: str, body: GroupIn, db=Depends(get_db)):
        if not groups_mod.set_selected(db, gid, body.selected):
            raise HTTPException(404, "unknown group")
        return {"ok": True}

    @app.post("/api/groups")
    def add_group(body: NewGroupIn, db=Depends(get_db)):
        try:
            gid = groups_mod.add_manual(db, body.url, body.name)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"id": gid}

    @app.post("/api/groups/discover")
    def start_discovery(body: DiscoverIn):
        """Run discover_groups.py in the background (it takes the scan lock itself)."""
        proc = discovery["proc"]
        if proc and proc.poll() is None:
            raise HTTPException(409, "already running")
        args = [sys.executable, str(PROJECT_DIR / "discover_groups.py"), "--user", uid]
        if body.score:
            args.append("--score")
        log = open(discover_log, "w", encoding="utf-8")
        discovery["proc"] = subprocess.Popen(args, cwd=PROJECT_DIR, stdout=log, stderr=subprocess.STDOUT,
                                             env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return {"running": True}

    @app.get("/api/groups/discover")
    def discovery_status():
        proc = discovery["proc"]
        running = bool(proc and proc.poll() is None)
        try:
            lines = discover_log.read_text(encoding="utf-8").strip().splitlines()[-5:]
        except OSError:
            lines = []
        return {"running": running, "exit_code": None if running or not proc else proc.returncode, "log": lines}

    # --- external AI (GPT) export / import -----------------------------------------------------

    @app.get("/api/gpt/export")
    def gpt_export(include_old: bool = False, db=Depends(get_db)):
        payload = gpt_io.export(db, cfg, user, include_old=include_old)
        return {**payload, "text": gpt_io.as_text(payload)}

    @app.post("/api/gpt/preview")
    def gpt_preview(body: ReplyIn, db=Depends(get_db)):
        return gpt_io.preview(db, uid, body.text)

    @app.post("/api/gpt/apply")
    def gpt_apply(body: ReplyIn, db=Depends(get_db)):
        return gpt_io.apply(db, uid, body.text, override_human=body.override_human)

    # --- background scheduler -------------------------------------------------------------------

    @app.get("/api/scheduler")
    def scheduler_state(db=Depends(get_db)):
        return {**scheduler.state, "settings": settings.get(db, "scheduler")}

    @app.post("/api/scheduler/run")
    def scheduler_run():
        if scheduler.state["running"]:
            raise HTTPException(409, "already running")
        scheduler.run_now()
        return {"ok": True}

    # --- notifications ----------------------------------------------------------------------

    @app.get("/api/notifications")
    def recent_notifications(db=Depends(get_db)):
        rows = db.execute("SELECT id, kind, post_id, title, body, created_at, sent_at, channels, error"
                          " FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 50", (uid,)).fetchall()
        tg = notify.telegram_config()
        return {"items": [dict(r) for r in rows],
                "telegram": {"configured": bool(tg), "from_watchdog": bool(tg and tg.get("from_watchdog"))},
                "push_devices": db.execute("SELECT count(*) FROM push_subscriptions WHERE user_id = ?",
                                           (uid,)).fetchone()[0]}

    @app.get("/api/push/key")
    def push_key():
        return {"public_key": notify.vapid_keys()["public_key"]}

    @app.post("/api/push/subscribe")
    def push_subscribe(body: PushSubIn, db=Depends(get_db)):
        notify.add_subscription(db, uid, body.model_dump())
        return {"ok": True}

    @app.post("/api/push/unsubscribe")
    def push_unsubscribe(body: PushSubIn, db=Depends(get_db)):
        notify.remove_subscription(db, body.endpoint)
        return {"ok": True}

    @app.put("/api/telegram")
    def set_telegram(body: TelegramIn):
        notify.save_telegram(body.token, body.chat_id)
        return {"ok": True}

    @app.post("/api/notifications/test")
    def test_notification(db=Depends(get_db)):
        conf = settings.get(db, "notifications")
        if not conf.get("enabled"):
            raise HTTPException(400, "notifications are turned off")
        notify.queue(db, uid, "test", f"test:{statuses.now_iso()}", "בדיקה מ-ApartmentBot", "ככה ייראו ההתראות.")
        if notify.in_quiet_hours(conf.get("quiet_hours")):
            return {"sent": 0, "error": None, "note": "quiet hours: it will be sent when they end"}
        n = notify.deliver_pending(db, uid)
        last = db.execute("SELECT error FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                          (uid,)).fetchone()
        return {"sent": n, "error": last[0] if last else None}

    # --- follow-ups -----------------------------------------------------------------------------

    @app.get("/api/followups")
    def due_followups(db=Depends(get_db)):
        due = followups.due(db, uid)
        if not due:
            return []
        cards = {c["post_id"]: c for c in feed.query_posts(
            db, cfg, uid, post_ids=[d["post_id"] for d in due], limit=len(due))["items"]}
        return [{**d, "card": cards.get(d["post_id"])} for d in due]

    @app.post("/api/followups/{post_id}/snooze")
    def snooze_followup(post_id: str, body: SnoozeIn, db=Depends(get_db)):
        try:
            followups.snooze(db, uid, post_id, body.hours)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True}

    @app.post("/api/followups/{post_id}/dismiss")
    def dismiss_followup(post_id: str, db=Depends(get_db)):
        try:
            followups.dismiss(db, uid, post_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True}

    # --- AI providers -------------------------------------------------------------------------

    def _ai_state(db):
        s = settings.get_all(db)
        keys = providers.load_keys()
        status = [{"id": p["id"], "has_key": bool(keys.get(p["id"])),
                   "remaining_today": RateLimiter(db, p["id"], p.get("per_minute"), p.get("per_day")).remaining_today()}
                  for p in s["ai_providers"]]
        return {"ai_providers": s["ai_providers"], "ai_stages": s["ai_stages"],
                "anthropic_limits": s["anthropic_limits"], "status": status, "presets": providers.PRESETS,
                "stages": list(providers.STAGES), "anthropic_model": cfg["app"]["ai_model"]}

    @app.get("/api/ai")
    def ai_settings(db=Depends(get_db)):
        return _ai_state(db)

    @app.put("/api/ai")
    def save_ai(body: AiIn, db=Depends(get_db)):
        ids = set()
        for p in body.ai_providers:
            if not p.get("id") or not p.get("base_url") or not p.get("model"):
                raise HTTPException(400, "every provider needs id, base_url and model")
            if p["id"] == providers.ANTHROPIC or p["id"] in ids:
                raise HTTPException(400, f"provider id {p['id']!r} is taken")
            ids.add(p["id"])
        bad = [v for v in body.ai_stages.values() if v != providers.ANTHROPIC and v not in ids]
        if bad:
            raise HTTPException(400, f"unknown provider in stages: {bad}")
        settings.put(db, {"ai_providers": body.ai_providers, "ai_stages": body.ai_stages,
                          "anthropic_limits": body.anthropic_limits})
        return _ai_state(db)

    @app.put("/api/ai/keys/{provider_id}")
    def save_ai_key(provider_id: str, body: KeyIn):
        providers.save_key(provider_id, body.key)
        return {"ok": True}

    # --- the web app ----------------------------------------------------------------------------

    @app.get("/{path:path}", include_in_schema=False)
    def web(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "unknown API route")
        dist = Path(web_dist).resolve()
        target = (dist / path).resolve()
        if path and dist in target.parents and target.is_file():
            no_cache = target.name in ("sw.js", "manifest.webmanifest")
            return FileResponse(target, headers={"Cache-Control": "no-cache"} if no_cache else None)
        index = dist / "index.html"
        if not index.is_file():
            return JSONResponse({"detail": "web app not built: cd web && npm install && npm run build"},
                                status_code=503)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
