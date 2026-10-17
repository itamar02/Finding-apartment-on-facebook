"""Travel-time estimate to the configured destination, for listings I approved.

A stronger model (commute.model, default claude-sonnet-5-5) estimates the door-to-door trip by
public transport, with a rental e-scooter (Lime and the like) allowed for the first or last
stretch. It has no live timetable or traffic data: the answer is an informed estimate, shown as
one and stored in post_commute (the posts themselves are never touched).
"""
import json
import threading

from . import statuses
from .ai_filter import _enum, _obj, _TEXT, _INT, cost_usd

DEFAULT_MODEL = "claude-sonnet-5-5"
APPROVED = ("interested", "needs_details", "contacted", "awaiting_reply", "meeting_scheduled", "visited",
            "top_candidate")

SCHEMA = _obj({
    "from_place": _TEXT,
    "minutes": _INT,
    "route": _TEXT,
    "uses_scooter": {"type": "boolean"},
    "scooter_minutes": _INT,
    "confidence": _enum("low", "medium", "high"),
    "station": _TEXT,
    "walk_minutes": _INT,
})

SYSTEM = """You estimate commute times in Israel for someone choosing an apartment.
You get the apartment's location (street / neighborhood / city, or hints from the ad) and a destination.
You have no live timetable or traffic data: use what you know of Israeli public transport (Dan, Egged, \
Metropoline, Israel Railways, the Tel Aviv light rail and its lines, typical bus frequencies) and of the \
geography, and give your best realistic estimate.

Rules:
- The trip is door to door: walking to the first stop, waiting, the ride, transfers, the walk at the end. \
Use the day and arrival time you are given (public transport runs differently at rush hour and on Fridays and Saturdays; on Shabbat there is almost no service).
- A rental e-scooter or e-bike (Lime, Wind, Bird, Tel-O-Fun bikes...) may be used for a stretch where it \
really helps (the first or last 1-3 km, or to skip a transfer). Mention it only if you use it, set \
uses_scooter and scooter_minutes, and do not assume one is waiting at the door.
- A destination written as "A/B" means either place is fine: estimate the one that is quicker and say \
which one in the route. A destination that is a landmark or a station: go to its entrance.
- from_place: the place you started from, in a few words (the street if known, else the neighborhood).
- If the location is only a city or too vague, still give an estimate for the middle of the area, \
confidence low. If there is no usable location at all, minutes = 0 and confidence low, route explains.
- station: the public-transport station or stop nearest to the apartment that the trip actually starts from (Hebrew name, e.g. "תחנת רכבת השלום" or "קו 18 ברחוב אלנבי"); walk_minutes: the walk from the apartment to it. Give them only when the location names a street (or an exact place); otherwise station = "" and walk_minutes = 0.
- route: Hebrew, at most two short sentences, the legs with approximate minutes, e.g. \
"הליכה 7 דק׳ → רכבת קלה אדום → קו 5 → הליכה 4 דק׳". No introduction.
- Be honest: confidence high only when the places and lines are clear to you."""


def settings(user):
    """{"model", "destinations": [{name, address, max_minutes, arrive_by, days}]} from the user's config.

    commute.destinations is the list; an older config with a single commute.destination / absolute_max_minutes
    is read as a one-item list."""
    c = user.get("commute") or {}
    dests = c.get("destinations")
    if dests is None:
        d = c.get("destination") or {}
        name = (d.get("name") or d.get("address") or "").strip()
        dests = [{"name": name, "address": d.get("address"), "max_minutes": c.get("absolute_max_minutes")}] if name else []
    out = [{"name": d["name"].strip(), "address": (d.get("address") or "").strip(), "max_minutes": d.get("max_minutes"),
            "arrive_by": d.get("arrive_by"), "days": d.get("days") or "weekdays"} for d in dests if (d.get("name") or "").strip()]
    return {"destinations": out, "model": c.get("model") or DEFAULT_MODEL}


DAY_TEXT = {"weekdays": "a normal weekday (Sunday to Thursday)", "daily": "any day", "weekend": "a Friday or Saturday"}


def names(user):
    return [d["name"] for d in settings(user)["destinations"]]


def _location_text(card):
    loc = card["location"]
    parts = [loc.get("text")] + (loc.get("hints") or [])
    return "; ".join(p for p in parts if p)


def check(db, cfg, user, router, post_id, force=False):
    """Estimate and store the trip to every configured destination. Returns the stored estimates (a list; empty
    when commute is not set up or the listing has no location at all)."""
    from . import feed
    s = settings(user)
    if not s["destinations"]:
        return []
    card = feed.post_detail(db, cfg, user["id"], post_id)
    if not card:
        return []
    place = _location_text(card)
    if not place:
        return []
    have = {c["destination"]: c for c in card["commutes"]}
    for d in s["destinations"]:
        old = have.get(d["name"])
        if old and not force and old["arrive_by"] == d["arrive_by"] and old["days"] == d["days"]:
            continue
        _estimate(db, user, router, post_id, place, d, s["model"])
    return feed.commutes(db, user["id"], post_id)


def _estimate(db, user, router, post_id, place, d, model):
    when = (f"Arrive by {d['arrive_by']} on {DAY_TEXT.get(d['days'], d['days'])}." if d["arrive_by"]
            else f"Plan for {DAY_TEXT.get(d['days'], d['days'])}, mid-morning, outside rush hour.")
    content = (f"Destination: {d['name']}" + (f" ({d['address']})" if d["address"] and d["address"] != d["name"] else "")
               + f"\nApartment location: {place}\n{when}\n"
               + (f"Maximum wanted: {d['max_minutes']} minutes.\n" if d["max_minutes"] else ""))
    raw, usage, cost, label = router.json_call("commute", [{"type": "text", "text": SYSTEM}], content, SCHEMA, model=model)
    r = json.loads(raw)
    minutes = r["minutes"] if r["minutes"] and r["minutes"] > 0 else None
    ok = None if minutes is None or not d["max_minutes"] else int(minutes <= d["max_minutes"])
    cost = cost if cost is not None else cost_usd(model, usage)
    station = (r["station"] or "").strip() or None
    with db:
        db.execute(
            "INSERT OR REPLACE INTO post_commute (post_id, user_id, destination, from_place, minutes, within_limit,"
            " route, uses_scooter, scooter_minutes, confidence, model, cost_usd, checked_at, station, walk_minutes,"
            " arrive_by, days) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (post_id, user["id"], d["name"], r["from_place"], minutes, ok, r["route"].strip(),
             int(r["uses_scooter"]), r["scooter_minutes"] if r["uses_scooter"] else None, r["confidence"],
             model, cost, statuses.now_iso(), station,
             r["walk_minutes"] if r["walk_minutes"] and r["walk_minutes"] > 0 and station else None,
             d["arrive_by"], d["days"]))


def approved_without_estimate(db, user_id, destinations):
    """Listings I approved, plus those the AI passed that nobody has closed, that lack an estimate for at least
    one destination (destinations: the list of names)."""
    names = [d for d in destinations if d]
    if not names:
        return []
    marks = ",".join("?" * len(APPROVED))
    ids = [r[0] for r in db.execute(f"SELECT post_id FROM post_status WHERE user_id = ? AND status IN ({marks})",
                                    (user_id, *APPROVED))]
    ids += [r[0] for r in db.execute(
        "SELECT a.post_id FROM post_analyses a LEFT JOIN post_status s ON s.post_id = a.post_id AND s.user_id = a.user_id"
        " WHERE a.user_id = ? AND a.status = 'ok' AND a.final_decision = 'PASS'"
        " AND coalesce(s.status, '') NOT IN ('not_relevant', 'taken')"
        " AND a.id = (SELECT max(b.id) FROM post_analyses b WHERE b.post_id = a.post_id AND b.user_id = a.user_id AND b.status = 'ok')",
        (user_id,))]
    have = {}
    for r in db.execute("SELECT post_id, destination FROM post_commute WHERE user_id = ?", (user_id,)):
        have.setdefault(r[0], set()).add(r[1])
    return [i for i in dict.fromkeys(ids) if not set(names) <= have.get(i, set())]


def check_in_background(cfg, user, open_db, router_factory, post_ids):
    """Estimate on a worker thread (a status change should not wait for the model). Errors are swallowed;
    a post that fails does not stop the others."""
    def work():
        try:
            db = open_db()
        except Exception:
            return
        try:
            router = router_factory(db)
            for post_id in post_ids:
                try:
                    check(db, cfg, user, router, post_id)
                except Exception:   # a missing key or an offline model must not break anything else
                    continue
        finally:
            db.close()
    if post_ids:
        threading.Thread(target=work, daemon=True).start()
