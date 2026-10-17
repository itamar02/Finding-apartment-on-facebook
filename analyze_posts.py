"""Run stored Facebook posts through AI extraction + filtering.

Usage:
    python analyze_posts.py --limit 5          # analyze up to 5 not-yet-analyzed posts
    python analyze_posts.py --all              # analyze every pending post
    python analyze_posts.py --post-id ID --force   # re-analyze a specific post

Already-analyzed posts are skipped unless --force is given or the pipeline
(prompt/schema) version changed. Needs an Anthropic API key: see app.api_key in config.yaml.
"""
import argparse
import json
import sys
from pathlib import Path

import anthropic

from apartment_bot import ai_filter, db as dbmod, jobs, providers
from apartment_bot.config import ConfigError, get_api_key, get_user, load_config
from apartment_bot.display import facts_lines, term_safe
from apartment_bot.pictures import download_pictures
from apartment_bot.pipeline import analyze_one, pending_posts, recheck_stored
from apartment_bot.report import build_entries, write_report


def _recheck(db, user):
    """Free: re-apply the duplicate / too-old rules to saved results (see recheck_stored)."""
    changed = recheck_stored(db, user)
    if changed:
        print(f"Re-checked saved results: {changed} listings are now rejected (duplicate / too old).")


def _commute(db, cfg, user, client):
    """The trip estimate for listings the AI just passed (best effort, a cent each)."""
    from apartment_bot import commute
    dest = commute.names(user)
    if not dest:
        return
    done = 0
    for pid in commute.approved_without_estimate(db, user["id"], dest)[:40]:
        try:
            done += len(commute.check(db, cfg, user, client, pid))
        except Exception as e:
            print(f"  trip estimate for {pid} failed: {e}")
    if done:
        print(f"Trip estimates added: {done}")


def main():
    """Analyze pending posts, print a terminal-safe report and token/cost totals."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--limit", type=int, help="maximum number of posts to analyze")
    group.add_argument("--all", action="store_true", help="analyze all pending posts")
    ap.add_argument("--post-id", action="append", help="restrict to these post IDs (repeatable)")
    ap.add_argument("--user", help="user id from config.yaml (default: the only user)")
    ap.add_argument("--force", action="store_true", help="re-analyze even if already analyzed")
    ap.add_argument("--report", type=Path,
                    help="also write a report of this run's posts (.json = JSON, else text); "
                         "for all stored results use export_results.py")
    args = ap.parse_args()

    try:
        cfg = load_config()
        user = get_user(cfg, args.user)
    except ConfigError as e:
        sys.exit(f"config error: {e}")

    db, backups = dbmod.migrate()
    for b in backups:
        print(f"Database migrated (backup: {Path(b).name})")

    posts = pending_posts(db, user["id"], limit=None if args.all else args.limit,
                          post_ids=args.post_id, force=args.force)
    if not posts:
        print("Nothing to analyze.")
        _recheck(db, user)
        return 0

    try:
        api_key = get_api_key(cfg)   # Anthropic is the default and the fallback for every stage
    except ConfigError as e:
        sys.exit(f"API key: {e}")
    # Each stage uses the provider chosen in the app (default: Anthropic); see providers.py.
    client = providers.Router(db, cfg, lambda: anthropic.Anthropic(api_key=api_key))
    model = cfg["app"]["ai_model"]
    early_stop = bool(cfg["app"].get("stop_at_first_dealbreaker"))
    print(f"Analyzing {len(posts)} posts for user '{user['id']}' with {model} "
          f"(pipeline {ai_filter.PIPELINE_VERSION}; stop at first dealbreaker: {'on' if early_stop else 'off'})\n")

    jobs.clear_stop()   # a leftover request from an earlier run must not stop this one
    if not jobs.acquire_analyze_lock():
        sys.exit("Another analysis is already running (it would spend the same money twice). Wait for it or stop it in the app.")
    try:
        return _analyze(db, cfg, user, args, client, model, early_stop, posts)
    finally:
        jobs.release_analyze_lock()


def _analyze(db, cfg, user, args, client, model, early_stop, posts):
    """The analysis loop and its summary (main() holds the analysis lock around it)."""
    results, total_cost, totals = [], 0.0, {"input_tokens": 0, "output_tokens": 0,
                                           "cache_read_tokens": 0, "cache_write_tokens": 0}
    counts = {}
    skipped_by_code = 0
    for i, post in enumerate(posts, 1):
        if jobs.stop_requested():
            print(f"Stopped on request after {i - 1} of {len(posts)} posts; each finished post is saved.")
            break
        row = analyze_one(db, client, model, user, post, early_stop=early_stop)
        print(f"[{i}/{len(posts)}] post {post['post_id']}  {post['url']}")
        if row["status"] != "ok":
            print(f"    ERROR: {row['error']}\n")
            counts["ERROR"] = counts.get("ERROR", 0) + 1
            continue
        counts[row["final_decision"]] = counts.get(row["final_decision"], 0) + 1
        if row.get("facts_json") is None:   # rejected by a code rule before the AI call
            skipped_by_code += 1
            print(f"    REJECT by code rule '{row['final_rule']}' (no AI call, no cost): "
                  f"{term_safe(row['final_reason'])}\n")
            results.append({"post_id": post["post_id"]})
            continue
        for k in totals:
            totals[k] += row[k] or 0
        total_cost += row["cost_usd"] or 0
        for line in facts_lines(json.loads(row["facts_json"])):
            print("    " + line)
        if row.get("ai_decision"):
            print(f"    AI decision: {row['ai_decision']} (rule: {row['ai_rule']})")
        else:
            print("    decided at the gate: AI classified the post, code applied the rule")
        print(f"    FINAL: {row['final_decision']}  decided by: {row['decided_by']}  rule: {row['final_rule']}")
        print(f"    why: {term_safe(row['final_reason'])}")
        print(f"    tokens: in {row['input_tokens']} / out {row['output_tokens']} / cache read "
              f"{row['cache_read_tokens']} / cache write {row['cache_write_tokens']}   "
              f"cost: {'$%.5f' % row['cost_usd'] if row['cost_usd'] is not None else 'unknown (model not in PRICING)'}\n")
        results.append({**{k: v for k, v in row.items() if not k.endswith("_json")},
                        "facts": json.loads(row["facts_json"]),
                        "hard_rules": json.loads(row["hard_rules_json"])})

    for note in dict.fromkeys(client.notes):
        print(f"Note: {note}")
    n_ok = len(results)
    n_ai = n_ok - skipped_by_code
    summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    print(f"Done: {summary}.")
    if skipped_by_code:
        print(f"{skipped_by_code} posts were rejected by code rules (duplicate / too old) without an AI call.")
    if n_ok:
        if n_ai:
            print(f"Tokens: in {totals['input_tokens']:,} / out {totals['output_tokens']:,} / cache read "
                  f"{totals['cache_read_tokens']:,} / cache write {totals['cache_write_tokens']:,}")
            print(f"Cost: ${total_cost:.4f} total, ${total_cost / n_ai:.5f} per AI-analyzed post")
        else:
            print("Cost: $0.0000 (no AI calls)")
        all_time = db.execute("SELECT coalesce(sum(cost_usd), 0) FROM post_analyses").fetchone()[0]
        print(f"All AI spend so far: ${all_time:.2f}")
        # Photos only for posts whose decision is in config pictures.save_for.
        # All qualifying listings, not just this run's: also covers a run that was stopped early.
        pics = download_pictures(db, cfg)
        if pics["downloaded"] or pics["failed"]:
            print(f"Photos: {pics['downloaded']} downloaded for {pics['posts']} listings"
                  + (f", {pics['failed']} failed (retry with download_pictures.py)" if pics["failed"] else ""))
    _recheck(db, user)
    _commute(db, cfg, user, client)
    if args.report:
        # Same format as export_results.py, limited to the posts analyzed in this run.
        write_report(build_entries(db, cfg, user["id"], post_ids=[r["post_id"] for r in results]),
                     args.report, user["id"])
        print(f"Report written to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
