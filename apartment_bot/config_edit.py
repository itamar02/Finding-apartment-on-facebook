"""Edit a user's settings in config.yaml from the app, keeping its comments and order.

Only the fields listed in EDITABLE can be changed. Every write:
  1. backs the file up as config.backup-<timestamp>.yaml (git-ignored),
  2. edits it with ruamel.yaml (comments, order and layout stay),
  3. validates the result with the normal config loader, and keeps the old file if it fails.
A changed config changes the user's config_hash, as designed: why.py then tells that a
stored decision was made with older settings.
"""
import io
import shutil
from datetime import datetime
from pathlib import Path

from ruamel.yaml import YAML

from .config import DEFAULT_CONFIG_PATH, LISTING_TYPES, ConfigError, get_user, load_config, validate_destinations

TEXT_LIST = "list[str]"
EDITABLE = {
    "profile.age": "int",
    "profile.gender": ("male", "female"),
    "listing.max_post_age_days": "num",
    "listing.allowed_types": ("list", sorted(LISTING_TYPES)),
    "listing.search_cities": TEXT_LIST,
    "budget.preferred_max_rent": "num",
    "budget.absolute_max_rent": "num",
    "budget.absolute_max_applies_to": ("rent", "rent_plus_known_costs"),
    "areas.allowed": TEXT_LIST,
    "areas.preferred": TEXT_LIST,
    "areas.less_preferred": TEXT_LIST,
    "areas.excluded": TEXT_LIST,
    "broker.policy": ("allow", "avoid", "reject"),
    "roommates.preferred_age_min": "int?",
    "roommates.preferred_age_max": "int?",
    "roommates.max_roommates": "int?",
    "roommates.gender_preference_mismatch": ("needs_review", "reject", "ignore"),
    "features.required": TEXT_LIST,
    "features.nice_to_have": TEXT_LIST,
    "features.dealbreakers": TEXT_LIST,
    "move_in.earliest": "date?",
    "move_in.latest": "date?",
    "commute.destinations": "destinations",
    "free_text": "str",
}


class EditError(ValueError):
    pass


def _get(d, path):
    for part in path.split("."):
        d = d.get(part) if isinstance(d, dict) else None
    return d


def read(cfg, user_id=None):
    """The editable values of one user, as {path: value}."""
    user = get_user(cfg, user_id)
    return {p: _get(user, p) for p in EDITABLE}


def check_value(path, value):
    """Return the value converted to the field's type, or raise EditError."""
    kind = EDITABLE.get(path)
    if kind is None:
        raise EditError(f"{path} can't be changed from the app")
    if isinstance(kind, tuple) and kind and kind[0] == "list":
        if not isinstance(value, list) or any(v not in kind[1] for v in value):
            raise EditError(f"{path}: allowed values are {', '.join(kind[1])}")
        return value
    if isinstance(kind, tuple):
        if value not in kind:
            raise EditError(f"{path}: must be one of {', '.join(kind)}")
        return value
    if kind == TEXT_LIST:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise EditError(f"{path}: must be a list of texts")
        return [v.strip() for v in value if v.strip()]
    if kind == "destinations":
        if not isinstance(value, list):
            raise EditError(f"{path}: must be a list")
        out = []
        for d in value:
            if not isinstance(d, dict):
                raise EditError(f"{path}: each destination must have a name")
            item = {"name": str(d.get("name") or "").strip()}
            if d.get("address"):
                item["address"] = str(d["address"]).strip()
            if d.get("max_minutes") not in (None, ""):
                try:
                    item["max_minutes"] = int(float(d["max_minutes"]))
                except (TypeError, ValueError):
                    raise EditError(f"{path}: the maximum minutes must be a number")
            if d.get("arrive_by"):
                item["arrive_by"] = str(d["arrive_by"]).strip()
            for k in ("lat", "lon"):
                if d.get(k) not in (None, ""):
                    try:
                        item[k] = float(d[k])
                    except (TypeError, ValueError):
                        raise EditError(f"{path}: {k} must be a number")
            item["days"] = d.get("days") or "weekdays"
            out.append(item)
        try:
            validate_destinations(out)
        except ConfigError as e:
            raise EditError(str(e))
        return out
    if kind.endswith("?") and value in (None, ""):
        return None
    base = kind.rstrip("?")
    try:
        if base == "int":
            return int(value)
        if base == "num":
            f = float(value)
            return int(f) if f.is_integer() else f
        if base == "date":
            return datetime.strptime(str(value), "%Y-%m-%d").date().isoformat()
        if base == "str":
            return str(value or "")
    except (TypeError, ValueError):
        raise EditError(f"{path}: not a valid {base}")
    raise EditError(f"{path}: unknown field type")


def _set(node, path, value):
    parts = path.split(".")
    for part in parts[:-1]:
        if part not in node or node[part] is None:
            node[part] = {}
        node = node[part]
    old = node.get(parts[-1])
    if path == "commute.destinations":
        node[parts[-1]] = value          # a list of mappings: written as a fresh block list
    elif isinstance(old, list) and isinstance(value, list):
        flow = old.fa.flow_style() or not old.ca.items
        old[:] = value            # same list object: keeps its comments
        if flow:
            old.fa.set_flow_style()   # lists stay on one line, as in the template
    else:
        node[parts[-1]] = value


def apply(changes, user_id=None, path=DEFAULT_CONFIG_PATH):
    """Apply [(path, op, value)] (op: set | add | remove) to one user. Returns (new cfg, backup path).

    add / remove work on text lists; set replaces the value. Raises EditError and leaves the
    file untouched when a change or the resulting config is invalid.
    """
    path = Path(path)
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)    # the layout of config.example.yaml
    yaml.representer.add_representer(
        type(None), lambda r, _d: r.represent_scalar("tag:yaml.org,2002:null", "null"))
    doc = yaml.load(path.read_text(encoding="utf-8"))
    users = doc.get("users") or []
    if user_id is None and len(users) == 1:
        user = users[0]
    else:
        user = next((u for u in users if u.get("id") == user_id), None)
    if user is None:
        raise EditError(f"no user {user_id!r} in config.yaml")
    for field, op, value in changes:
        if op == "set":
            _set(user, field, check_value(field, value))
        elif op in ("add", "remove"):
            if EDITABLE.get(field) not in (TEXT_LIST,) and not (isinstance(EDITABLE.get(field), tuple)
                                                                and EDITABLE[field][0] == "list"):
                raise EditError(f"{field}: add/remove only work on lists")
            current = list(_get(user, field) or [])
            if op == "add" and value not in current:
                current.append(value)
            if op == "remove":
                current = [v for v in current if v != value]
            _set(user, field, check_value(field, current))
        else:
            raise EditError(f"unknown operation {op!r}")
    buf = io.StringIO()
    yaml.dump(doc, buf)
    newline = "\r\n" if b"\r\n" in path.read_bytes() else "\n"   # keep the file's line endings
    new_text = buf.getvalue().replace("\r\n", "\n").replace("\n", newline)
    candidate = path.with_name(path.stem + ".candidate.yaml")
    candidate.write_bytes(new_text.encode("utf-8"))
    try:
        new_cfg = load_config(candidate)
    except ConfigError as e:
        raise EditError(f"the change would make the config invalid: {e}")
    finally:
        candidate.unlink(missing_ok=True)
    backup = path.with_name(f"{path.stem}.backup-{datetime.now():%Y%m%d-%H%M%S}.yaml")
    shutil.copy2(path, backup)
    path.write_bytes(new_text.encode("utf-8"))
    return new_cfg, backup
