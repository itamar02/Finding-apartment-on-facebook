"""Optional AI rating of how useful a Facebook group is for this search (0-10, with a reason).

One cheap request for up to 40 groups at a time, using only each group's name, description,
size and privacy - no posts and no personal data.
"""
import json

import anthropic

from . import groups as groups_mod
from .ai_filter import cost_usd
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
    client = anthropic.Anthropic(api_key=get_api_key(cfg))
    model = cfg["app"]["ai_model"]
    rated, cost = 0, 0.0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        listing = json.dumps([{"id": g["id"], "name": g["name"], "description": g["description"],
                               "members": g["member_count"], "privacy": g["privacy"]} for g in chunk],
                             ensure_ascii=False)
        resp = client.messages.create(
            model=model, max_tokens=8000,
            messages=[{"role": "user", "content": PROMPT.format(
                cities=", ".join(user["listing"]["search_cities"]),
                types=", ".join(user["listing"]["allowed_types"]), groups=listing)}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
        u = resp.usage
        cost += cost_usd(model, {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                                 "cache_read_tokens": u.cache_read_input_tokens or 0,
                                 "cache_write_tokens": u.cache_creation_input_tokens or 0}) or 0.0
        text = next(b.text for b in resp.content if b.type == "text")
        ids = {g["id"] for g in chunk}
        for r in json.loads(text)["groups"]:
            if r["id"] in ids:
                groups_mod.set_relevance(db, r["id"], max(0, min(10, r["score"])), r["reason"])
                rated += 1
    return rated, cost
