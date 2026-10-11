"""Runs posts through AI extraction (stage 1) and hard-rule verification (stage 2)."""
import json
from datetime import datetime

from . import ai_filter, hard_rules
from .config import user_config_hash
from .db import EFFECTIVE_TEXT_SQL

_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def pending_posts(db, user_id, limit=None, post_ids=None, force=False):
    """Posts without a successful analysis for this user and pipeline version, newest first."""
    # Listings the human rejected by hand never need the AI.
    # An edited post (post_versions) is analyzed with its current text, and again after an edit.
    edited = _has_versions(db)
    text_sql = EFFECTIVE_TEXT_SQL if edited else "p.text"
    sql = (f"SELECT p.post_id, p.group_id, p.author, p.author_id, p.posted_at, p.posted_at_raw,"
           f" {text_sql} AS text, p.text_truncated, p.url, p.first_seen_at"
           " FROM posts p WHERE coalesce(p.text, '') != ''"
           " AND NOT EXISTS (SELECT 1 FROM human_decisions h WHERE h.post_id = p.post_id AND h.user_id = ?"
           " AND h.decision = 'REJECT')")
    params = [user_id]
    if not force:
        newer_version = (" AND a.analyzed_at >= coalesce((SELECT max(v.seen_at) FROM post_versions v"
                         " WHERE v.post_id = p.post_id), '')") if edited else ""
        sql += (" AND NOT EXISTS (SELECT 1 FROM post_analyses a WHERE a.post_id = p.post_id"
                " AND a.user_id = ? AND a.pipeline_version = ? AND a.status = 'ok'" + newer_version + ")")
        params += [user_id, ai_filter.PIPELINE_VERSION]
        if not post_ids:   # listings an AI chat already sorted don't need the AI bot (named post ids still do)
            sql += AI_CHAT_COVERED
            params.append(user_id)
    if post_ids:
        sql += f" AND p.post_id IN ({','.join('?' * len(post_ids))})"
        params += list(post_ids)
    sql += " ORDER BY p.posted_at DESC, p.post_id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return db.execute(sql, params).fetchall()


# A listing the user's AI chat already gave a status is covered: the paid analysis is skipped for it.
AI_CHAT_COVERED = (" AND NOT EXISTS (SELECT 1 FROM post_status g WHERE g.post_id = p.post_id AND g.user_id = ?"
                   " AND g.source = 'gpt')")


def count_pending(db, user_id):
    """How many posts still wait for the AI (same rule as pending_posts), without loading them."""
    edited = _has_versions(db)
    newer = (" AND a.analyzed_at >= coalesce((SELECT max(v.seen_at) FROM post_versions v"
             " WHERE v.post_id = p.post_id), '')") if edited else ""
    return db.execute(
        "SELECT count(*) FROM posts p WHERE coalesce(p.text, '') != ''"
        " AND NOT EXISTS (SELECT 1 FROM human_decisions h WHERE h.post_id = p.post_id AND h.user_id = ?"
        " AND h.decision = 'REJECT')"
        " AND NOT EXISTS (SELECT 1 FROM post_analyses a WHERE a.post_id = p.post_id AND a.user_id = ?"
        " AND a.pipeline_version = ? AND a.status = 'ok'" + newer + ")" + AI_CHAT_COVERED,
        (user_id, user_id, ai_filter.PIPELINE_VERSION, user_id)).fetchone()[0]


def _has_versions(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name = 'post_versions'").fetchone())


def analyze_one(db, client, model, user, post, now=None, early_stop=False):
    """Analyze one post and store the result row. Returns the stored row as a dict.

    Order of work, cheapest first:
      1. code rules that need no facts (duplicate, too old)  -> no AI call
      2. if early_stop: the gate - the AI only says "rental offer?" and the listing type, and
         the CODE rejects on those two rules when the AI is confident
      3. if early_stop: a quick AI first pass; a *confirmed* rejection ends the analysis
      4. otherwise the full AI extraction + decision
    A rejection counts as confirmed when the code checks agree with the AI (hard_rules.combine).
    """
    now = now or datetime.now().astimezone()
    row = {
        "post_id": post["post_id"], "user_id": user["id"],
        "pipeline_version": ai_filter.PIPELINE_VERSION, "config_hash": user_config_hash(user),
        "model": model, "analyzed_at": now.isoformat(timespec="seconds"),
    }
    pre = hard_rules.pre_ai_rejection(db, post, user, now)
    if pre:   # decided by code alone - no AI call, no cost
        row.update(
            model="none (code rule)", status="ok", final_decision="REJECT", final_rule=pre["rule"],
            final_reason=pre["detail"], decided_by="hard_rule", hard_rules_json=json.dumps([pre], ensure_ascii=False),
            input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.0)
        _insert(db, row)
        return row

    spent = {"usage": dict.fromkeys(_USAGE_KEYS, 0), "cost": 0.0}   # across all AI calls for this post
    args = (client, model, user, post["text"], post["posted_at"], now.date().isoformat())
    try:
        if early_stop:
            gate = ai_filter.analyze_post(*args, mode="gate")
            _spend(spent, gate[2], gate[3])
            _label(row, gate[2])
            rejection = _gate_rejection(gate[0], user)
            if rejection:
                return _store_gate(db, row, gate[0], gate[1], rejection, spent)
            quick = ai_filter.analyze_post(*args, mode="triage")
            _spend(spent, quick[2], quick[3])
            _label(row, quick[2])
            judged = _judge(db, post, quick[0], user, now)
            if judged[1][0] == "REJECT":
                return _store(db, row, quick[0], quick[1], judged, spent)
        full = ai_filter.analyze_post(*args, mode="full")
        _spend(spent, full[2], full[3])
        _label(row, full[2])
    except ai_filter.AnalysisError as e:
        msg, usage, cost = e.args
        _spend(spent, usage, cost)
        row.update(status="error", error=msg, **_spend_cols(spent))
        _insert(db, row)
        return row
    except Exception as e:  # API/network errors: record and move on; retried next run
        row.update(status="error", error=f"{type(e).__name__}: {e}", **_spend_cols(spent))
        _insert(db, row)
        return row
    return _store(db, row, full[0], full[1], _judge(db, post, full[0], user, now), spent)


def recheck_stored(db, user, now=None):
    """Re-run the code rules that depend on other posts or on the clock, on saved results.

    A listing analyzed earlier can become a duplicate later (its twin in another group was
    scanned afterwards) or simply get too old. For every PASS / NEEDS_REVIEW listing this
    runs hard_rules.pre_ai_rejection again; if it now fails, a new analysis row is stored
    with decision REJECT (decided_by hard_rule, no AI, no cost) that keeps the facts and
    the AI's original verdict for explainability. Idempotent. Returns how many changed.
    """
    now = now or datetime.now().astimezone()
    latest = db.execute(
        "SELECT a.* FROM post_analyses a WHERE a.user_id = ? AND a.status = 'ok' AND a.final_decision != 'REJECT'"
        " AND a.id = (SELECT max(b.id) FROM post_analyses b WHERE b.post_id = a.post_id"
        "             AND b.user_id = a.user_id AND b.status = 'ok')", (user["id"],)).fetchall()
    changed = 0
    for old in latest:
        post = db.execute("SELECT * FROM posts WHERE post_id = ?", (old["post_id"],)).fetchone()
        pre = hard_rules.pre_ai_rejection(db, post, user, now)
        if not pre:
            continue
        _insert(db, {
            "post_id": old["post_id"], "user_id": user["id"], "pipeline_version": old["pipeline_version"],
            "config_hash": user_config_hash(user), "model": "none (recheck)",
            "analyzed_at": now.isoformat(timespec="seconds"), "status": "ok",
            "ai_decision": old["ai_decision"], "ai_rule": old["ai_rule"], "ai_reason": old["ai_reason"],
            "final_decision": "REJECT", "final_rule": pre["rule"], "final_reason": pre["detail"],
            "decided_by": "hard_rule", "facts_json": old["facts_json"],
            "hard_rules_json": json.dumps([pre], ensure_ascii=False),
            "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.0})
        changed += 1
    # AI rejections that were sent to review only because the code could not find the AI's quote:
    # with the tolerant quote check they become rejections.
    unverified = db.execute(
        "SELECT a.* FROM post_analyses a WHERE a.user_id = ? AND a.status = 'ok' AND a.final_decision = 'NEEDS_REVIEW'"
        " AND a.ai_decision = 'REJECT' AND a.decided_by = 'hard_rule'"
        " AND a.id = (SELECT max(b.id) FROM post_analyses b WHERE b.post_id = a.post_id"
        "             AND b.user_id = a.user_id AND b.status = 'ok')", (user["id"],)).fetchall()
    for old in unverified:
        post = db.execute("SELECT * FROM posts WHERE post_id = ?", (old["post_id"],)).fetchone()
        checks = hard_rules.run_checks(db, post, json.loads(old["facts_json"]), user, now)
        decision, rule, reason, by = hard_rules.combine({"decision": old["ai_decision"], "rule": old["ai_rule"]}, checks)
        if decision != "REJECT":
            continue
        _insert(db, {
            "post_id": old["post_id"], "user_id": user["id"], "pipeline_version": old["pipeline_version"],
            "config_hash": user_config_hash(user), "model": "none (recheck)",
            "analyzed_at": now.isoformat(timespec="seconds"), "status": "ok",
            "ai_decision": old["ai_decision"], "ai_rule": old["ai_rule"], "ai_reason": old["ai_reason"],
            "final_decision": "REJECT", "final_rule": rule, "final_reason": reason,
            "decided_by": by, "facts_json": old["facts_json"],
            "hard_rules_json": json.dumps(checks, ensure_ascii=False),
            "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.0})
        changed += 1
    return changed


def _gate_rejection(gate, user):
    """(checks, failing check) if the gate answer is confident and breaks a rule, else None.

    The AI only classifies; the configured rules (rental offer, allowed listing types) are
    applied by the same code checks used everywhere else. Low confidence never rejects.
    """
    if gate["confidence"] != "high":
        return None
    facts = {"is_rental_offer": gate["is_rental_offer"], "listing_type": gate["listing_type"]}
    checks = [hard_rules.check_rental_offer(facts), hard_rules.check_listing_type(facts, user)]
    failed = [c for c in checks if c["status"] == "fail"]
    return (checks, failed[0]) if failed else None


def _store_gate(db, row, gate, raw_text, rejection, spent):
    """Store a rejection decided at the gate: the code rule's reason, only two facts."""
    checks, failed = rejection
    facts = ai_filter.normalize_facts(ai_filter._blank_to_none(ai_filter.partial_to_full(
        {"is_rental_offer": gate["is_rental_offer"], "listing_type": gate["listing_type"]})))
    facts["_partial"] = True
    row.update(
        status="ok", final_decision="REJECT", final_rule=failed["rule"], final_reason=failed["detail"],
        decided_by="hard_rule", facts_json=json.dumps(facts, ensure_ascii=False),
        hard_rules_json=json.dumps(checks, ensure_ascii=False), raw_response_json=raw_text, **_spend_cols(spent))
    _insert(db, row)
    return row


def _judge(db, post, parsed, user, now):
    """Run the code checks on the AI's facts and merge with its decision -> (checks, combined)."""
    checks = hard_rules.run_checks(db, post, parsed["facts"], user, now)
    return checks, hard_rules.combine(parsed["decision"], checks)


def _store(db, row, parsed, raw_text, judged, spent):
    checks, (decision, rule, reason, by) = judged
    ai = parsed["decision"]
    row.update(
        status="ok", ai_decision=ai["decision"], ai_rule=ai["rule"], ai_reason=ai["reason"],
        final_decision=decision, final_rule=rule, final_reason=reason, decided_by=by,
        facts_json=json.dumps(parsed["facts"], ensure_ascii=False),
        hard_rules_json=json.dumps(checks, ensure_ascii=False),
        raw_response_json=raw_text, **_spend_cols(spent))
    _insert(db, row)
    return row


def _label(row, usage):
    """Record which provider/model answered (several stages may use different ones)."""
    label = usage.get("model_label")
    if not label:
        return
    if row.get("_labels") is None:
        row["_labels"] = []
    if label not in row["_labels"]:
        row["_labels"].append(label)
    row["model"] = " + ".join(row["_labels"])


def _spend(spent, usage, cost):
    for k in _USAGE_KEYS:
        spent["usage"][k] += usage[k]
    spent["cost"] = None if spent["cost"] is None or cost is None else spent["cost"] + cost


def _spend_cols(spent):
    return {**spent["usage"], "cost_usd": spent["cost"]}


def _insert(db, row):
    row.pop("_labels", None)
    cols = list(row)
    with db:
        db.execute(f"INSERT INTO post_analyses ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                   [row[c] for c in cols])
