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
Assume a normal weekday morning (arriving around 8:30) unless told otherwise.
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
    c = user.get("commute") or {}
    d = c.get("destination") or {}
    name = (d.get("name") or "").strip()
    return {"destination": name or (d.get("address") or "").strip(), "address": (d.get("address") or "").strip(),
            "limit": c.get("absolute_max_minutes"), "preferred": c.get("preferred_max_minutes"),
            "model": c.get("model") or DEFAULT_MODEL}


def _location_text(card):
    loc = card["location"]
    parts = [loc.get("text")] + (loc.get("hints") or [])
    return "; ".join(p for p in parts if p)


def check(db, cfg, user, router, post_id, force=False):
    """Estimate and store the trip for one post. Returns the stored dict, or None when commute is not set up
    (no destination) or the listing has no location at all."""
    from . import feed
    s = settings(user)
    if not s["destination"]:
        return None
    card = feed.post_detail(db, cfg, user["id"], post_id)
    if not card:
        return None
    if card.get("commute") and not force and card["commute"]["destination"] == s["destination"]:
        return card["commute"]
    place = _location_text(card)
    if not place:
        return None
    content = (f"Destination: {s['destination']}" + (f" ({s['address']})" if s["address"] and s["address"] != s["destination"] else "")
               + f"\nApartment location: {place}\n"
               + (f"Maximum wanted: {s['limit']} minutes.\n" if s["limit"] else ""))
    raw, usage, cost, label = router.json_call("commute", [{"type": "text", "text": SYSTEM}], content, SCHEMA,
                                               model=s["model"])
    r = json.loads(raw)
    minutes = r["minutes"] if r["minutes"] and r["minutes"] > 0 else None
    ok = None if minutes is None or not s["limit"] else int(minutes <= s["limit"])
    cost = cost if cost is not None else cost_usd(s["model"], usage)
    with db:
        db.execute(
            "INSERT OR REPLACE INTO post_commute (post_id, user_id, destination, from_place, minutes, within_limit,"
            " route, uses_scooter, scooter_minutes, confidence, model, cost_usd, checked_at, station, walk_minutes)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (post_id, user["id"], s["destination"], r["from_place"], minutes, ok, r["route"].strip(),
             int(r["uses_scooter"]), r["scooter_minutes"] if r["uses_scooter"] else None, r["confidence"],
             s["model"], cost, statuses.now_iso(), (r["station"] or "").strip() or None,
             r["walk_minutes"] if r["walk_minutes"] and r["walk_minutes"] > 0 and (r["station"] or "").strip() else None))
    return feed.post_detail(db, cfg, user["id"], post_id)["commute"]


def approved_without_estimate(db, user_id, destination):
    marks = ",".join("?" * len(APPROVED))
    return [r[0] for r in db.execute(
        f"SELECT s.post_id FROM post_status s LEFT JOIN post_commute c ON c.post_id = s.post_id AND c.user_id = s.user_id"
        f" AND c.destination = ? WHERE s.user_id = ? AND s.status IN ({marks}) AND c.post_id IS NULL",
        (destination, user_id, *APPROVED))]


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
