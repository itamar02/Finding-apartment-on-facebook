"""Optional AI rating of how useful a Facebook group is for this search (0-10, with a reason).

One cheap request for up to 40 groups at a time, using only each group's name, description,
size and privacy - no posts and no personal data.
"""
import json

import anthropic

from . import groups as groups_mod, providers
from .config import get_api_key

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["groups"],
    "properties": {"groups": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "score", "reason"],
        "properties": {"id": {"type": "string"}, "score": {"type": "integer"}, "reason": {"type": "string"}},
    }}},
}

PROMPT = """I am looking for a place to rent through Facebook groups in Israel.
Cities: {cities}. Listing types I accept: {types}.
Rate each group below from 0 (useless for this search) to 10 (exactly the right place to find
such listings). Consider the city/area, whether it is about rentals or roommates, and size.
Give a short reason in Hebrew. Return every id exactly once.

Groups:
{groups}"""


def score_unscored(db, cfg, user, batch=40):
    """Rate every group without a score. Returns (count rated, cost in USD)."""
    todo = [g for g in groups_mod.list_groups(db) if g["relevance_score"] is None]
    if not todo:
        return 0, 0.0
    key = get_api_key(cfg)
    router = providers.Router(db, cfg, lambda: anthropic.Anthropic(api_key=key))   # stage "group_score"
    rated, cost = 0, 0.0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        listing = json.dumps([{"id": g["id"], "name": g["name"], "description": g["description"],
                               "members": g["member_count"], "privacy": g["privacy"]} for g in chunk],
                             ensure_ascii=False)
        prompt = PROMPT.format(cities=", ".join(user["listing"]["search_cities"]),
                               types=", ".join(user["listing"]["allowed_types"]), groups=listing)
        text, _usage, call_cost, _label = router.json_call(
            "group_score", [{"type": "text", "text": "You rate Facebook groups. Answer in JSON."}], prompt, SCHEMA)
        cost += call_cost or 0.0
        ids = {g["id"] for g in chunk}
        for r in json.loads(text)["groups"]:
            if r["id"] in ids:
                groups_mod.set_relevance(db, r["id"], max(0, min(10, r["score"])), r["reason"])
                rated += 1
    return rated, cost
