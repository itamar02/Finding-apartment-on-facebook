"""Load and validate config.yaml. All user-specific values come from there."""
import hashlib
import json
import os
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

LISTING_TYPES = {"whole_apartment", "roommate_search", "room_replacement", "sublet"}


class ConfigError(Exception):
    pass


def _require(d, path, types, optional=False):
    """Fetch a dotted key from a user dict and check its type.

    optional=True allows the value to be blank (null) - meaning "no preference".
    """
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise ConfigError(f"missing setting: {path}")
        cur = cur[part]
    if cur is None and optional:
        return None
    if not isinstance(cur, types):
        raise ConfigError(f"setting {path} has the wrong type: {cur!r}")
    return cur


def _validate_user(u):
    """Check that one user entry has every setting the pipeline reads, with sane values."""
    uid = _require(u, "id", str)
    num = (int, float)
    _require(u, "profile.age", int)
    if _require(u, "profile.gender", str) not in ("male", "female"):
        raise ConfigError(f"[{uid}] profile.gender must be 'male' or 'female'")
    _require(u, "listing.max_post_age_days", num)
    types = _require(u, "listing.allowed_types", list)
    bad = set(types) - LISTING_TYPES
    if bad:
        raise ConfigError(f"[{uid}] unknown listing.allowed_types: {sorted(bad)}")
    _require(u, "listing.search_cities", list)
    pref = _require(u, "budget.preferred_max_rent", num)
    absolute = _require(u, "budget.absolute_max_rent", num)
    if pref > absolute:
        raise ConfigError(f"[{uid}] budget.preferred_max_rent is above budget.absolute_max_rent")
    if _require(u, "budget.absolute_max_applies_to", str) not in ("rent", "rent_plus_known_costs"):
        raise ConfigError(f"[{uid}] budget.absolute_max_applies_to must be 'rent' or 'rent_plus_known_costs'")
    for key in ("allowed", "preferred", "less_preferred", "excluded"):
        _require(u, f"areas.{key}", list)
    if _require(u, "broker.policy", str) not in ("allow", "avoid", "reject"):
        raise ConfigError(f"[{uid}] broker.policy must be allow | avoid | reject")
    if _require(u, "roommates.gender_preference_mismatch", str) not in ("needs_review", "reject", "ignore"):
        raise ConfigError(f"[{uid}] roommates.gender_preference_mismatch must be needs_review | reject | ignore")
    for key in ("required", "nice_to_have", "dealbreakers"):
        _require(u, f"features.{key}", list)
    # Commute values are not used by any stage yet, so all of them may be blank.
    _require(u, "commute.destination.name", str, optional=True)
    for key in ("preferred_max_minutes", "absolute_max_minutes", "preferred_max_walking_minutes",
                "preferred_max_transfers"):
        _require(u, f"commute.{key}", num, optional=True)


def _validate_api_key_setting(cfg):
    """app.api_key is optional: source env (default) or config; checked for shape only."""
    setting = cfg["app"].get("api_key")
    if setting is None:
        return
    if not isinstance(setting, dict):
        raise ConfigError("app.api_key must be a mapping with source / env_var / value")
    if setting.get("source", "env") not in ("env", "config"):
        raise ConfigError("app.api_key.source must be 'env' or 'config'")
    for key in ("env_var", "value"):
        if setting.get(key) is not None and not isinstance(setting[key], str):
            raise ConfigError(f"app.api_key.{key} must be text")


def _windows_user_env(name):
    """A user environment variable as Windows has it stored. A program started before the
    variable was set (or from a tool that doesn't refresh it) doesn't see it in os.environ."""
    if os.name != "nt":
        return ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, name)[0]).strip()
    except OSError:
        return ""


def get_api_key(cfg):
    """The Anthropic API key, from the environment variable or from config.yaml.

    app.api_key.source: env    -> read the variable named app.api_key.env_var
                                  (default ANTHROPIC_API_KEY)
    app.api_key.source: config -> use app.api_key.value
    Raises ConfigError with a plain explanation when the key is missing. Called only by
    commands that use the AI, so scanning and exporting work without a key.
    """
    setting = cfg["app"].get("api_key") or {}
    source = setting.get("source", "env")
    if source == "config":
        key = (setting.get("value") or "").strip()
        if not key:
            raise ConfigError("app.api_key.source is 'config' but app.api_key.value is empty - "
                              "put your key there, or switch source to 'env'")
        return key
    var = setting.get("env_var") or "ANTHROPIC_API_KEY"
    key = os.environ.get(var, "").strip() or _windows_user_env(var)
    if not key:
        raise ConfigError(f"the environment variable {var} is not set (app.api_key.source is 'env'). "
                          f"Set it, or switch to source: config and put the key in config.yaml")
    return key


def load_config(path=DEFAULT_CONFIG_PATH):
    """Parse config.yaml and validate it. Raises ConfigError with a readable message."""
    try:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigError(f"{Path(path).name} not found. Copy config.example.yaml to config.yaml "
                          "and edit it (config.yaml holds your personal settings and is not committed to git)")
    if not isinstance(cfg, dict) or not isinstance(cfg.get("users"), list) or not cfg["users"]:
        raise ConfigError("config must contain a non-empty 'users' list")
    _require(cfg, "app.ai_model", str)
    _require(cfg, "app.stop_at_first_dealbreaker", bool, optional=True)
    _validate_api_key_setting(cfg)
    _require(cfg, "browser.chrome_path", str)
    _require(cfg, "browser.profile_dir", str)
    _require(cfg, "browser.debug_port", int)
    _require(cfg, "pictures.folder", str)
    bad = set(_require(cfg, "pictures.save_for", list)) - {"PASS", "NEEDS_REVIEW", "REJECT"}
    if bad:
        raise ConfigError(f"pictures.save_for has unknown decisions: {sorted(bad)}")
    # The groups to scan now live in the database (groups.py); this list only seeds it once,
    # so it may be empty.
    groups = _require(cfg, "facebook.groups", list)
    if not all(isinstance(g, dict) and isinstance(g.get("id"), str) and g["id"] for g in groups):
        raise ConfigError("facebook.groups must be a list of {id: \"...\", name: \"...\"}")
    if len({g["id"] for g in groups}) != len(groups):
        raise ConfigError("facebook.groups contains the same group id twice")
    ids = [u.get("id") for u in cfg["users"]]
    if len(set(ids)) != len(ids):
        raise ConfigError("user ids must be unique")
    for u in cfg["users"]:
        _validate_user(u)
    return cfg


def max_post_age_days(cfg):
    """Largest max_post_age_days across users - how far back the scanner needs to read."""
    return max(u["listing"]["max_post_age_days"] for u in cfg["users"])


def get_user(cfg, user_id=None):
    """Return the user entry with this id, or the only user when user_id is None."""
    if user_id is None:
        if len(cfg["users"]) > 1:
            raise ConfigError("several users configured; pass --user")
        return cfg["users"][0]
    for u in cfg["users"]:
        if u["id"] == user_id:
            return u
    raise ConfigError(f"no user with id {user_id!r} in config")


def user_config_hash(user):
    """Stable fingerprint of a user's settings, stored with each analysis."""
    blob = json.dumps(user, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
