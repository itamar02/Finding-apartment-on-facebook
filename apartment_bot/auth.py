"""Login for the phone app: a long random token, handed to the phone with a one-time code.

Tailscale already keeps the server private; this is a second lock. The token lives in
secrets/app_token.txt (git-ignored). `python app_server.py --pair` prints an 8-digit code
that is valid for 10 minutes and for one use; the app exchanges it for the token, which
is kept in an HttpOnly cookie, so it never appears in a URL.
"""
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

SECRETS_DIR = Path(__file__).resolve().parent.parent / "secrets"
TOKEN_FILE = SECRETS_DIR / "app_token.txt"
PAIRING_FILE = SECRETS_DIR / "pairing.json"
COOKIE = "apartmentbot_token"
PAIRING_MINUTES = 10
PAIRING_TRIES = 5


def _hash(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def get_token(reset=False):
    """The app token, created on first use (or replaced with reset=True)."""
    SECRETS_DIR.mkdir(exist_ok=True)
    if reset or not TOKEN_FILE.exists():
        TOKEN_FILE.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


def token_ok(candidate):
    return bool(candidate) and hmac.compare_digest(candidate, get_token())


def new_pairing_code():
    """Create a one-time pairing code; only its hash is stored."""
    SECRETS_DIR.mkdir(exist_ok=True)
    code = f"{secrets.randbelow(10**8):08d}"
    PAIRING_FILE.write_text(json.dumps({"hash": _hash(code), "expires": time.time() + PAIRING_MINUTES * 60,
                                        "tries": 0}), encoding="utf-8")
    return code


def redeem_pairing_code(code):
    """The token if the code is right and still valid, else None. Wrong tries are counted."""
    try:
        state = json.loads(PAIRING_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if time.time() > state["expires"] or state["tries"] >= PAIRING_TRIES:
        PAIRING_FILE.unlink(missing_ok=True)
        return None
    if hmac.compare_digest(_hash((code or "").strip()), state["hash"]):
        PAIRING_FILE.unlink(missing_ok=True)
        return get_token()
    state["tries"] += 1
    PAIRING_FILE.write_text(json.dumps(state), encoding="utf-8")
    return None
