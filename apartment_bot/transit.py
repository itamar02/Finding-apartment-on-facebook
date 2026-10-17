"""Real public-transport routing with OpenTripPlanner on the Ministry of Transport's timetable (GTFS).

Setup: python setup_transit.py (downloads the timetable, the Israel map and OpenTripPlanner into transit/, builds
the graph once). The planner runs locally on 127.0.0.1:8099 and is started on demand. Addresses become
coordinates with OpenStreetMap's Nominatim (cached in the database, one request a second).
"""
import json
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from . import statuses

TRANSIT_DIR = Path(__file__).resolve().parent.parent / "transit"
OTP_PORT = 8099
OTP_URL = f"http://127.0.0.1:{OTP_PORT}"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "ApartmentBot/1.0 (personal apartment search)"
SCOOTER_PENALTY_MIN = 2          # unlocking the scooter

_last_geocode = [0.0]


class TransitError(Exception):
    pass


def available():
    return (TRANSIT_DIR / "graph.obj").exists() and (TRANSIT_DIR / "otp.jar").exists()


def _java():
    home = os.environ.get("JAVA_HOME")
    cands = [Path(home) / "bin" / "java.exe"] if home else []
    cands.append(Path(r"C:\Program Files\Android\Android Studio\jbr\bin\java.exe"))
    for c in cands:
        if c.exists():
            return str(c)
    return shutil.which("java")


def _up():
    try:
        urllib.request.urlopen(f"{OTP_URL}/otp/", timeout=2)
        return True
    except Exception:
        return False


def ensure_server(wait=240):
    """Start the planner if it is not running (a few seconds to a minute to load the graph)."""
    if _up():
        return
    if not available():
        raise TransitError("the transit planner is not set up: run python setup_transit.py")
    java = _java()
    if not java:
        raise TransitError("no Java found for the transit planner")
    log = open(TRANSIT_DIR / "otp.log", "ab")
    subprocess.Popen([java, "-Xmx8g", "-jar", str(TRANSIT_DIR / "otp.jar"), "--load", "--port", str(OTP_PORT),
                      "--securePort", str(OTP_PORT + 1), str(TRANSIT_DIR)], stdout=log, stderr=log,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    end = time.time() + wait
    while time.time() < end:
        if _up():
            return
        time.sleep(2)
    raise TransitError("the transit planner did not start (see transit/otp.log)")


# --- addresses -> coordinates ---------------------------------------------------------------------

STREET_TYPES = {"residential", "tertiary", "secondary", "primary", "unclassified", "pedestrian", "living_street",
                "service", "house", "apartments", "yes", "building", "road", "footway", "cycleway", "path"}


def geocode(db, text):
    """(lat, lon, precision) for a place text, or None. precision: street | area. Cached forever."""
    key = " ".join((text or "").split())
    if not key:
        return None
    row = db.execute("SELECT lat, lon, precision FROM geocode_cache WHERE query = ?", (key,)).fetchone()
    if row:
        return (row[0], row[1], row[2]) if row[0] is not None else None
    wait = 1.1 - (time.time() - _last_geocode[0])
    if wait > 0:
        time.sleep(wait)
    query = key if ("israel" in key.lower() or "ישראל" in key) else key + ", ישראל"
    url = NOMINATIM + "?" + urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": 1,
                                                     "countrycodes": "il", "accept-language": "he"})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8"))
    except Exception as e:
        raise TransitError(f"address lookup failed: {e}")
    finally:
        _last_geocode[0] = time.time()
    hit = data[0] if data else None
    precision = None
    if hit:
        precision = "street" if hit.get("type") in STREET_TYPES else "area"
    with db:
        db.execute("INSERT OR REPLACE INTO geocode_cache (query, lat, lon, precision, name, cached_at) VALUES (?,?,?,?,?,?)",
                   (key, float(hit["lat"]) if hit else None, float(hit["lon"]) if hit else None, precision,
                    hit.get("display_name") if hit else None, statuses.now_iso()))
    return (float(hit["lat"]), float(hit["lon"]), precision) if hit else None


# --- routing -----------------------------------------------------------------------------------

DAY_SETS = {"weekdays": (6, 0, 1, 2, 3), "daily": (0, 1, 2, 3, 4, 5, 6), "weekend": (4, 5)}   # Python weekday(): Mon=0


def next_date(days, today=None):
    today = today or date.today()
    ok = DAY_SETS.get(days or "weekdays", DAY_SETS["weekdays"])
    d = today + timedelta(days=1)
    while d.weekday() not in ok:
        d += timedelta(days=1)
    return d


_QUERY = """
query($from: InputCoordinates!, $to: InputCoordinates!, $date: String!, $time: String!) {
  plan(from: $from, to: $to, date: $date, time: $time, arriveBy: true, numItineraries: 3,
       transportModes: [{mode: TRANSIT}, {mode: %s}]) {
    itineraries {
      duration
      legs { mode duration distance from { name } to { name } route { shortName longName } }
    }
  }
}
"""


def _gql(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(f"{OTP_URL}/otp/gtfs/v1", data=body, headers={"Content-Type": "application/json"})
    try:
        out = json.loads(urllib.request.urlopen(req, timeout=90).read().decode("utf-8"))
    except Exception as e:
        raise TransitError(f"the transit planner failed: {e}")
    if out.get("errors"):
        raise TransitError(f"the transit planner refused the request: {out['errors'][0].get('message')}")
    return (out["data"]["plan"] or {}).get("itineraries") or []


def _plan(origin, dest, when, bike):
    return _gql(_QUERY % ("BICYCLE" if bike else "WALK"),
                {"from": {"lat": origin[0], "lon": origin[1]}, "to": {"lat": dest[0], "lon": dest[1]},
                 "date": when.strftime("%Y-%m-%d"), "time": when.strftime("%H:%M")})


def _describe(it):
    """Hebrew route text, the first transit stop and the minutes to reach it."""
    parts, station, before = [], None, 0
    for leg in it["legs"]:
        mins = max(1, round(leg["duration"] / 60))
        if leg["mode"] == "WALK":
            parts.append(f"הליכה {mins} דק׳")
        elif leg["mode"] == "BICYCLE":
            parts.append(f"🛴 {mins} דק׳")
        else:
            r = leg.get("route") or {}
            name = r.get("shortName") or r.get("longName") or leg["mode"]
            kind = {"RAIL": "רכבת", "SUBWAY": "רכבת קלה", "TRAM": "רכבת קלה", "BUS": "קו"}.get(leg["mode"], "")
            parts.append(f"{kind} {name} ({mins} דק׳)".strip())
            if station is None:
                station = (leg.get("from") or {}).get("name")
        if station is None:
            before += mins
    return " → ".join(parts), station, before or None


def estimate(db, place, dest, arrive_by, days):
    """Best trip from the place text to the destination (a dict with name/address/lat/lon): {minutes, route,
    station, walk_minutes, scooter, scooter_minutes, precision}, or None when nothing is found.
    Raises TransitError when the planner is down."""
    origin = geocode(db, place)
    if dest.get("lat") is not None and dest.get("lon") is not None:
        target = (float(dest["lat"]), float(dest["lon"]), "street")
    else:
        target = geocode(db, dest.get("address") or dest["name"])
    if not origin or not target:
        return None
    ensure_server()
    when = datetime.combine(next_date(days), datetime.strptime(arrive_by or "10:00", "%H:%M").time())
    best = None
    for bike in (False, True):
        for it in _plan(origin[:2], target[:2], when, bike):
            uses_bike = any(leg["mode"] == "BICYCLE" for leg in it["legs"])
            if bike and not uses_bike:
                continue
            mins = round(it["duration"] / 60) + (SCOOTER_PENALTY_MIN if uses_bike else 0)
            if best is None or mins < best[0]:
                best = (mins, it, uses_bike)
    if not best:
        return None
    mins, it, bike = best
    route, station, before = _describe(it)
    scooter_min = sum(round(leg["duration"] / 60) for leg in it["legs"] if leg["mode"] == "BICYCLE") if bike else None
    return {"minutes": mins, "route": route, "station": station, "walk_minutes": before, "scooter": bike,
            "scooter_minutes": scooter_min, "precision": origin[2]}
