"""Several posts in one gate request, for a provider with a small request quota."""
import json

from apartment_bot import ai_filter, pipeline


class FakeBatchRouter:
    stages = {"gate": "openrouter"}

    def __init__(self, skip=()):
        self.calls, self.skip = [], set(skip)

    def json_call(self, stage, system, content, schema):
        self.calls.append((stage, content))
        n = content.count("<post index=")
        results = [{"index": i, "is_rental_offer": True, "listing_type": "whole_apartment", "confidence": "high"}
                   for i in range(n) if i not in self.skip]
        return json.dumps({"results": results}), {"input_tokens": 100, "output_tokens": 20,
                                                  "cache_read_tokens": 0, "cache_write_tokens": 0}, 0.0, "fake:model"


def _posts(db):
    return db.execute("SELECT post_id, text, posted_at, group_id, author, author_id, url, first_seen_at "
                      "FROM posts WHERE post_id IN ('1001', '1002', '1003')").fetchall()


def test_one_request_answers_several_posts(db, cfg):
    user = cfg["users"][0]
    router = FakeBatchRouter()
    got = pipeline.batch_gates(db, router, user, _posts(db), size=10)
    assert set(got) == {"1001", "1002", "1003"} and len(router.calls) == 1
    parsed, raw, usage, cost = got["1001"]
    assert parsed["listing_type"] == "whole_apartment" and usage["model_label"] == "fake:model"
    assert usage["input_tokens"] == 33                      # the request's tokens are shared between its posts


def test_chunks_and_skipped_posts(db, cfg):
    user = cfg["users"][0]
    router = FakeBatchRouter(skip={1})
    got = pipeline.batch_gates(db, router, user, _posts(db), size=2)
    assert len(router.calls) == 2                            # 3 posts, 2 per request
    assert len(got) == 2                                     # the post the model skipped is asked about alone later


def test_anthropic_gate_stage_is_not_batched(db, cfg):
    class Plain(FakeBatchRouter):
        stages = {"gate": "anthropic"}
    assert pipeline.batch_gates(db, Plain(), cfg["users"][0], _posts(db)) == {}


def test_analyze_one_uses_the_batched_gate(db, cfg):
    user = cfg["users"][0]
    router = FakeBatchRouter()
    post = _posts(db)[0]
    gate = ({"is_rental_offer": False, "listing_type": "other", "confidence": "high"}, "{}",
            {"input_tokens": 5, "output_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0, "model_label": "fake"}, 0.0)
    row = pipeline.analyze_one(db, router, "m", user, post, early_stop=True, gate_pre=gate)
    assert row["final_decision"] == "REJECT" and not router.calls     # decided on the batched answer, no new call
