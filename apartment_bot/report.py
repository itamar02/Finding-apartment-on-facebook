"""Build and write a results report from stored data (read-only: no AI, no scanning).

Sections, in order: PASSED, NEEDS REVIEW, then a separator line, then HUMAN REJECTED
(listings you rejected by hand) and REJECTED (rejected automatically).
Each listing shows the decision, reason, contact details, key facts, photos and text,
using each post's latest successful analysis for the user.
"""
import json
from datetime import datetime

from . import human as human_mod
from .contacts import contact_info
from .display import facts_lines, keep_text

SECTIONS = [("PASS", "passed", "PASSED"), ("NEEDS_REVIEW", "needs_review", "NEEDS REVIEW"),
            ("HUMAN_REJECTED", "human_rejected", "HUMAN REJECTED (by you)"),
            ("REJECT", "rejected", "REJECTED")]
KEPT_IN_FULL = ("PASS", "NEEDS_REVIEW")   # sections shown with all facts and photos


def build_entries(db, cfg, user_id, post_ids=None):
    """One dict per analyzed post (latest successful analysis), newest post first.

    Listings the human rejected by hand (reject.py) get the decision HUMAN_REJECTED, which
    wins over the automatic one - even for posts that were never analyzed.
    """
    group_names = {g["id"]: g.get("name") for g in cfg["facebook"]["groups"]}
    human = human_mod.rejected(db, user_id)
    sql = ("SELECT a.*, p.group_id, p.author, p.author_id, p.posted_at, p.text, p.url"
           " FROM post_analyses a JOIN posts p ON p.post_id = a.post_id"
           " WHERE a.user_id = ? AND a.status = 'ok' AND a.id = (SELECT max(b.id) FROM post_analyses b"
           "   WHERE b.post_id = a.post_id AND b.user_id = a.user_id AND b.status = 'ok')")
    params = [user_id]
    if post_ids is not None:
        sql += f" AND a.post_id IN ({','.join('?' * len(post_ids))})"
        params += list(post_ids)
    rows = list(db.execute(sql, params))
    seen = {r["post_id"] for r in rows}
    unanalyzed = [p for p in human if p not in seen and (post_ids is None or p in post_ids)]
    if unanalyzed:
        rows += db.execute(
            "SELECT p.post_id, p.group_id, p.author, p.author_id, p.posted_at, p.text, p.url,"
            " NULL AS final_decision, NULL AS final_reason, NULL AS final_rule, NULL AS facts_json,"
            " NULL AS analyzed_at, NULL AS model FROM posts p"
            f" WHERE p.post_id IN ({','.join('?' * len(unanalyzed))})", unanalyzed).fetchall()
    entries = [_entry(db, group_names, r, human) for r in rows]
    entries.sort(key=lambda e: (e["posted_at"] or "", e["post_id"]), reverse=True)
    return entries


def _entry(db, group_names, r, human):
    """One report entry from a joined analysis/post row (see build_entries)."""
    decision, reason, rule = r["final_decision"], r["final_reason"], r["final_rule"]
    if r["post_id"] in human:
        note, when = human[r["post_id"]]
        reason = (f"Rejected by you on {when[:10]}" + (f": {note}" if note else "")
                  + f" (automatic decision: {decision or 'not analyzed'})")
        decision, rule = "HUMAN_REJECTED", "human"
    photos = db.execute("SELECT local_path, more_hidden FROM post_images WHERE post_id = ? ORDER BY idx",
                        (r["post_id"],)).fetchall()
    return {
        "decision": decision,
        "reason": reason,
        "rule": rule,
        "post_id": r["post_id"],
        "url": r["url"],
        "posted_at": r["posted_at"],
        "group": group_names.get(r["group_id"], r["group_id"]),
        "contact": contact_info(r),
        "facts": json.loads(r["facts_json"]) if r["facts_json"] else None,  # None: rejected by code / never analyzed
        "photos": {
            "downloaded": [p["local_path"] for p in photos if p["local_path"]],
            "in_feed": len(photos),
            "more_on_facebook": photos[0]["more_hidden"] if photos else 0,
        },
        "text": r["text"],
        "analyzed_at": r["analyzed_at"],
        "model": r["model"],
    }


def _sectioned(entries):
    return {key: [e for e in entries if e["decision"] == dec] for dec, key, _ in SECTIONS}


def write_json(entries, path, user_id):
    sections = _sectioned(entries)
    out = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "user": user_id,
           "counts": {k: len(v) for k, v in sections.items()}, **sections}
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


def _listing_block(e, full=True):
    """Text block for one listing; full=False (rejected) skips the extracted-facts lines."""
    f, c, ph = e["facts"], e["contact"], e["photos"]
    if f is None:   # rejected by a code rule before any AI call, or never analyzed: no extracted facts
        head = f"[{e['decision']}] no AI details stored ({e['rule']})"
    else:
        rent = f"{f['rent_ils']:,} ILS" if f["rent_ils"] else "rent not stated"
        if f["total_monthly_ils_known"] and f["total_monthly_ils_known"] != f["rent_ils"]:
            rent += f" (with extras: {f['total_monthly_ils_known']:,} ILS)"
        head = f"[{e['decision']}] {f['listing_type'].replace('_', ' ')} | {rent} | {keep_text(f['location_summary_en'])}"
    lines = [
        head,
        f"Why: {e['reason']}",
        f"Link: {e['url']}",
        f"Posted: {(e['posted_at'] or 'unknown')[:16].replace('T', ' ')}   Group: {e['group']}",
        f"Best contact: {c['best']}",
        f"Contact: {c['name'] or '-'}"
        + (f"   Phone: {', '.join(c['phones'])}" if c["phones"] else "")
        + (f"   WhatsApp: {', '.join(c['whatsapp'])}" if c["whatsapp"] else ""),
        f"Facebook profile: {c['facebook_profile'] or '- (anonymous or page post)'}",
    ]
    if full and f is not None:
        lines += ["  " + l for l in facts_lines(f, term_safe=keep_text)]
    if full and ph["in_feed"]:
        got = f"{len(ph['downloaded'])} downloaded" if ph["downloaded"] else "not downloaded"
        more = f", {ph['more_on_facebook']} more on Facebook" if ph["more_on_facebook"] else ""
        lines.append(f"Photos: {ph['in_feed']} in feed ({got}{more})")
        lines += [f"  {p}" for p in ph["downloaded"]]
    lines += ["Text:"] + ["  " + l for l in (e["text"] or "").splitlines()]
    return "\n".join(lines)


def write_text(entries, path, user_id):
    sections = _sectioned(entries)
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    head = (f"ApartmentBot results - user '{user_id}' - {now}\n"
            + "   ".join(f"{title}: {len(sections[key])}" for _, key, title in SECTIONS) + "\n")
    parts = [head]
    for dec, key, title in SECTIONS:
        if dec == "HUMAN_REJECTED":   # everything below the line is rejected (by you, then automatically)
            parts.append("\n" + "-" * 80 + "\n")
        parts.append(f"\n===== {title} ({len(sections[key])}) =====\n")
        for e in sections[key]:
            parts.append("\n" + _listing_block(e, full=(dec in KEPT_IN_FULL)) + "\n\n" + "." * 40 + "\n")
    path.write_text("".join(parts), encoding="utf-8")


def write_report(entries, path, user_id):
    """Write JSON if the file name ends in .json, otherwise a readable text report."""
    (write_json if path.suffix.lower() == ".json" else write_text)(entries, path, user_id)
