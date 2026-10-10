"""App settings stored in the `app_settings` table (JSON values), with defaults in code.

Settings that already belong to config.yaml (profile, budget, areas, ...) stay there; this
table holds only what the phone app adds.
"""
import json

from .statuses import now_iso

DEFAULTS = {
    # Prefilled WhatsApp opening message. Placeholders: {name} {area} {street} {group} {price}.
    # A part in [square brackets] is dropped when a placeholder in it is unknown.
    "whatsapp_template": "היי[ {name}], ראיתי את המודעה שלך בקבוצה {group} על הדירה[ ב{area}][ ב-{price} ₪]. היא עדיין רלוונטית?",
    # Posts older than this are hidden in the feed and left out of exports unless asked for.
    # None means: use the largest listing.max_post_age_days in config.yaml.
    "recent_days": None,
    # Searches used by discover_groups.py. None means: "דירות להשכרה <city>" and
    # "שותפים <city>" for every city in listing.search_cities.
    "group_search_queries": None,
}


def get_all(db):
    """Every setting: the stored value, or the default."""
    out = dict(DEFAULTS)
    for key, value in db.execute("SELECT key, value_json FROM app_settings"):
        if key in DEFAULTS:
            out[key] = json.loads(value)
    return out


def get(db, key):
    return get_all(db)[key]


def put(db, values):
    """Store some settings. Unknown keys are refused."""
    unknown = set(values) - set(DEFAULTS)
    if unknown:
        raise KeyError(f"unknown settings: {', '.join(sorted(unknown))}")
    now = now_iso()
    with db:
        for key, value in values.items():
            db.execute("INSERT OR REPLACE INTO app_settings (key, value_json, updated_at) VALUES (?,?,?)",
                       (key, json.dumps(value, ensure_ascii=False), now))
    return get_all(db)


def recent_days(db, cfg):
    """How many days count as recent (the feed's default window and recent-only exports)."""
    from .config import max_post_age_days
    stored = get(db, "recent_days")
    return stored if stored else max_post_age_days(cfg)


def group_queries(db, user):
    """The group searches to run (stored list, or two per search city)."""
    stored = get(db, "group_search_queries")
    if stored:
        return [q for q in stored if q.strip()]
    cities = user["listing"]["search_cities"]
    return [f"דירות להשכרה {c}" for c in cities] + [f"שותפים {c}" for c in cities]
