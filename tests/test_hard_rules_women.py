"""'דירת שותפות' = a women's apartment: rejected for a male user without the AI."""
from datetime import datetime

from apartment_bot import hard_rules


def test_womens_apartment_phrase_rejects_a_male_user(db, cfg):
    user = cfg["users"][0]
    user["profile"]["gender"] = "male"
    db.execute("UPDATE posts SET text = 'מתפנה חדר בדירת שותפות בפלורנטין' WHERE post_id = '1001'")
    post = db.execute("SELECT * FROM posts WHERE post_id = '1001'").fetchone()
    got = hard_rules.pre_ai_rejection(db, post, user, datetime.now().astimezone())
    assert got and got["rule"] == "gender_eligibility"
    user["profile"]["gender"] = "female"
    assert hard_rules.pre_ai_rejection(db, post, user, datetime.now().astimezone()) is None
