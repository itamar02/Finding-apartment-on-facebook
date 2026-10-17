"""Stage 2: deterministic verification of absolute rules.

The AI understands the listing; this module sanity-checks the simple absolute
rules in plain code, using the AI's structured facts, the raw post and the user's
config. A failed check forces REJECT even if the AI said PASS. Checks are cheap
and can be re-run at any time against the stored facts.
"""
import hashlib
import re
from datetime import datetime

from .textnorm import normalize

LISTING_TYPES = {"whole_apartment", "roommate_search", "room_replacement", "sublet"}


def _check(rule, status, detail):
    return {"rule": rule, "status": status, "detail": detail}


def _norm(s):
    return normalize(s)   # no emoji, single spaces: old copies without emoji still match new ones


def text_fingerprint(text):
    return hashlib.sha256(_norm(text).lower().encode("utf-8")).hexdigest()


def _quote_in_post(quote, post_text):
    """The AI's evidence quote is in the post. The AI often adds its own gloss after the quote
    ("מחפשת שותפה (feminine verb...)"), or joins two quotes with ";", so any such piece counts."""
    if not quote:
        return False
    text = _norm(post_text)
    pieces = [quote]
    for part in re.split(r"[;|]", quote):
        pieces += [part, re.sub(r"\s*[(\[].*$", "", part)]   # the part without a trailing "(...)" gloss
    return any(len(_norm(x)) >= 3 and _norm(x) in text for x in pieces)


def check_duplicate(db, post, max_age_days, now):
    """Same text as an earlier post (by another post ID) - the same ad in another group, or a repost.

    The earlier copy only counts while it is still fresh (within max_age_days): a repost of
    an ad whose first copy is already too old is the live listing and must not be dropped.
    """
    mine = (post["posted_at"] or post["first_seen_at"], post["post_id"])
    for other_id, other_posted, other_seen in _fingerprint_index(db).get(text_fingerprint(post["text"]), []):
        if other_id == post["post_id"]:
            continue
        if other_posted and (now - datetime.fromisoformat(other_posted)).total_seconds() > max_age_days * 86400:
            continue   # earlier copy is stale: it will be rejected for age, so this one is the live ad
        if (other_posted or other_seen, other_id) < mine:
            return _check("duplicate", "fail",
                          f"Rejected because the post text is identical to earlier post {other_id}.")
    return _check("duplicate", "pass", "No earlier post with identical text.")


_index_cache = {"db": None, "index": None}


def _fingerprint_index(db):
    """{text fingerprint: [(post_id, posted_at, first_seen_at)]}, built once per connection.

    Hashing every stored post for every post checked would be quadratic. Posts stored after
    the index was built are not in it; they are picked up by the next process.
    """
    if _index_cache["db"] is not db:
        index = {}
        for r in db.execute("SELECT post_id, posted_at, first_seen_at, text FROM posts WHERE coalesce(text, '') != ''"):
            index.setdefault(text_fingerprint(r["text"]), []).append((r["post_id"], r["posted_at"], r["first_seen_at"]))
        _index_cache.update(db=db, index=index)
    return _index_cache["index"]


def check_post_age(post, user, now):
    max_days = user["listing"]["max_post_age_days"]
    if not post["posted_at"]:
        return _check("post_age", "skipped", "Post date unknown.")
    age_days = (now - datetime.fromisoformat(post["posted_at"])).total_seconds() / 86400
    if age_days > max_days:
        return _check("post_age", "fail",
                      f"Rejected because the post is {age_days:.1f} days old and the configured maximum "
                      f"post age is {max_days} days.")
    return _check("post_age", "pass", f"Post is {age_days:.1f} days old (max {max_days}).")


def check_rental_offer(facts):
    if facts["is_rental_offer"] is False:
        return _check("not_a_rental_offer", "fail", "Rejected because the post does not offer a place to rent.")
    return _check("not_a_rental_offer", "pass", "Post offers a place to rent.")


def check_listing_type(facts, user):
    lt = facts["listing_type"]
    allowed = user["listing"]["allowed_types"]
    if lt not in LISTING_TYPES:
        return _check("listing_type", "skipped", f"Listing type is {lt}.")
    if lt not in allowed:
        return _check("listing_type", "fail",
                      f"Rejected because the listing is a {lt.replace('_', ' ')} and the configured "
                      f"allowed types are: {', '.join(allowed)}.")
    return _check("listing_type", "pass", f"Listing type {lt} is allowed.")


WOMEN_ONLY_PHRASES = ("דירת שותפות",)   # feminine plural: an apartment of women, looking for a woman


def check_women_only_phrase(post, user):
    """"דירת שותפות" means a women's apartment: rejected for a male user without needing the AI."""
    if user["profile"]["gender"] != "male":
        return _check("gender_eligibility", "pass", "Not a male user.")
    text = _norm(post["text"])
    for phrase in WOMEN_ONLY_PHRASES:
        if phrase in text:
            return _check("gender_eligibility", "fail",
                          f"Rejected because the post says \"{phrase}\" (a women's apartment) and the configured user is male.")
    return _check("gender_eligibility", "pass", "No women-only phrase.")


def check_gender(facts, user, post_text):
    rm = facts["roommates"]
    gender = user["profile"]["gender"]
    req = rm["required_gender"]
    if rm["gender_strictness"] != "strict" or req not in ("male", "female"):
        return _check("gender_eligibility", "pass", "No strict roommate gender requirement.")
    if req == gender:
        return _check("gender_eligibility", "pass", f"Strict requirement for a {req} roommate matches.")
    if not _quote_in_post(rm["gender_evidence"], post_text):
        return _check("gender_eligibility", "unverified",
                      f"AI reports a strict {req}-only requirement, but its quoted evidence "
                      f"{rm['gender_evidence']!r} was not found in the post.")
    return _check("gender_eligibility", "fail",
                  f"Rejected because the advertiser explicitly requires a {req} roommate "
                  f"(\"{rm['gender_evidence']}\") and the configured user is {gender}.")


def check_age(facts, user, post_text):
    rm = facts["roommates"]
    age = user["profile"]["age"]
    lo, hi = rm["age_min"], rm["age_max"]
    if rm["age_strictness"] != "strict" or (lo is None and hi is None):
        return _check("age_eligibility", "pass", "No strict roommate age requirement.")
    if (lo is None or age >= lo) and (hi is None or age <= hi):
        return _check("age_eligibility", "pass", f"Configured age {age} is within the required range.")
    rng = f"{lo}+" if hi is None else (f"up to {hi}" if lo is None else f"{lo}-{hi}")
    if not _quote_in_post(rm["age_evidence"], post_text):
        return _check("age_eligibility", "unverified",
                      f"AI reports a strict age requirement ({rng}), but its quoted evidence "
                      f"{rm['age_evidence']!r} was not found in the post.")
    return _check("age_eligibility", "fail",
                  f"Rejected because the listing requires age {rng} and the configured user age is {age}.")


def check_rent(facts, user):
    b = user["budget"]
    limit = b["absolute_max_rent"]
    rent, total = facts["rent_ils"], facts["total_monthly_ils_known"]
    if b["absolute_max_applies_to"] == "rent_plus_known_costs" and total is not None:
        # A lower bound when some extra has no stated amount - still "definitely above".
        amount = total
        if facts["additional_monthly_ils"] is None and not facts["extras_with_unknown_amount"]:
            label = "rent (no extra monthly costs stated)"
        else:
            label = "rent plus stated monthly costs" + (" (at least)" if facts["extras_with_unknown_amount"] else "")
    else:
        amount, label = rent, "rent"
    if amount is None:
        return _check("rent_absolute_max", "skipped", "Rent not stated.")
    if amount > limit:
        return _check("rent_absolute_max", "fail",
                      f"Rejected because the {label} is {amount:,} ILS and the configured absolute maximum "
                      f"is {limit:,} ILS.")
    return _check("rent_absolute_max", "pass", f"The {label} ({amount:,} ILS) is within {limit:,} ILS.")


def pre_ai_rejection(db, post, user, now):
    """Rejections that need no AI facts: duplicate text, post too old.

    Run before the AI call, so those posts cost nothing. The decision is the same one
    run_checks() would reach afterwards (same checks, same order). Returns the failing
    check, or None.
    """
    for check in (check_duplicate(db, post, user["listing"]["max_post_age_days"], now), check_post_age(post, user, now),
                  check_women_only_phrase(post, user)):
        if check["status"] == "fail":
            return check
    return None


# Order matters: the first failing check becomes the stated rejection reason.
def run_checks(db, post, facts, user, now):
    return [
        check_duplicate(db, post, user["listing"]["max_post_age_days"], now),
        check_post_age(post, user, now),
        check_women_only_phrase(post, user),
        check_rental_offer(facts),
        check_listing_type(facts, user),
        check_gender(facts, user, post["text"]),
        check_age(facts, user, post["text"]),
        check_rent(facts, user),
    ]


def combine(ai_decision, checks):
    """Merge the AI decision with code checks into (decision, rule, reason, decided_by)."""
    fails = [c for c in checks if c["status"] == "fail"]
    if fails:
        by = "ai+hard_rule" if ai_decision["decision"] == "REJECT" else "hard_rule"
        return "REJECT", fails[0]["rule"], fails[0]["detail"], by
    unverified = {c["rule"]: c for c in checks if c["status"] == "unverified"}
    if ai_decision["decision"] == "REJECT" and ai_decision["rule"] in unverified:
        c = unverified[ai_decision["rule"]]
        return "NEEDS_REVIEW", c["rule"], "AI rejected, but code could not verify it: " + c["detail"], "hard_rule"
    return ai_decision["decision"], ai_decision["rule"], ai_decision["reason"], "ai"
