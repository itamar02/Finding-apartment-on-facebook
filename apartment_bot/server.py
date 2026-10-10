"""The phone app's server: JSON API + the built web app (web/dist). Started by app_server.py.

Every /api route except /api/pair needs the app token (cookie or "Authorization: Bearer").
Changes also need the header "X-ApartmentBot: 1", which a page from another site cannot
send without the browser asking first, so a link elsewhere can't change anything here.
"""
import asyncio
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import auth, db as dbmod, feed, gpt_io, groups as groups_mod, settings, statuses
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


class ReplyIn(BaseModel):
    text: str
    override_human: bool = False


def create_app(cfg, user_id=None, db_path=dbmod.DB_PATH, web_dist=WEB_DIST, secure_cookie=True):
    user = get_user(cfg, user_id)
    uid = user["id"]
    app = FastAPI(title="ApartmentBot", docs_url=None, redoc_url=None, openapi_url=None)

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
