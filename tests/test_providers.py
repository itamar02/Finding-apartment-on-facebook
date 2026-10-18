"""Rate limiter and the provider switch (schema check, retry, fallback)."""
import io
import json

import pytest

from apartment_bot import providers, settings
from apartment_bot.ratelimit import RateLimited, RateLimiter

SCHEMA = {"type": "object", "required": ["decision", "n"], "additionalProperties": False,
          "properties": {"decision": {"type": "string", "enum": ["PASS", "REJECT"]}, "n": {"type": "integer"}}}


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def test_per_minute_limit_queues(db):
    clock = Clock()
    rl = RateLimiter(db, "groq", per_minute=2, clock=clock, sleep=clock.sleep)
    rl.acquire()
    clock.t += 10
    rl.acquire()
    rl.acquire()                       # third call in the minute waits for the first to age out
    assert clock.slept and 49 < clock.slept[0] < 51


def test_per_day_limit_raises(db):
    clock = Clock()
    rl = RateLimiter(db, "groq", per_day=2, clock=clock, sleep=clock.sleep)
    rl.acquire()
    rl.acquire()
    assert rl.remaining_today() == 0
    with pytest.raises(RateLimited):
        rl.acquire()


def test_limits_are_per_provider(db):
    clock = Clock()
    RateLimiter(db, "a", per_day=1, clock=clock).acquire()
    RateLimiter(db, "b", per_day=1, clock=clock).acquire()     # not blocked by "a"


def test_validate():
    providers.validate({"decision": "PASS", "n": 3}, SCHEMA)
    for bad in ({"decision": "MAYBE", "n": 3}, {"decision": "PASS"}, {"decision": "PASS", "n": "3"}, []):
        with pytest.raises(providers.InvalidOutput):
            providers.validate(bad, SCHEMA)


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_returning(*contents):
    calls = []

    def opener(req, timeout):
        calls.append(json.loads(req.data))
        content = contents[min(len(calls) - 1, len(contents) - 1)]
        return FakeResp(json.dumps({"choices": [{"message": {"content": content}}],
                                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode())
    opener.calls = calls
    return opener


class FakeAnthropic:
    label = "claude-test"

    def __init__(self):
        self.calls = 0

    def call(self, system, content, schema, max_tokens=16000):
        self.calls += 1
        return json.dumps({"decision": "REJECT", "n": 1}), {"input_tokens": 1, "output_tokens": 1,
                                                             "cache_read_tokens": 0, "cache_write_tokens": 0}, 0.001


def make_router(db, cfg, opener, key="k"):
    settings.put(db, {"ai_providers": [{"id": "groq", "base_url": "https://x/v1", "model": "m",
                                        "json_mode": "json_object", "per_minute": 100, "per_day": 100}],
                      "ai_stages": {"gate": "groq"}})
    r = providers.Router(db, cfg, lambda: None, keys={"groq": key} if key else {}, opener=opener)
    fake = FakeAnthropic()
    r._anthropic = fake
    return r, fake


SYSTEM = [{"type": "text", "text": "sys"}]


def test_openai_compatible_answer_used(db, cfg):
    opener = opener_returning('```json\n{"decision": "PASS", "n": 2}\n```')
    r, fake = make_router(db, cfg, opener)
    text, usage, cost, label = r.json_call("gate", SYSTEM, "post", SCHEMA)
    assert json.loads(text) == {"decision": "PASS", "n": 2} and label == "groq:m" and fake.calls == 0
    assert opener.calls[0]["response_format"] == {"type": "json_object"}
    assert "JSON schema" in opener.calls[0]["messages"][0]["content"]


def test_invalid_answer_retried_then_falls_back(db, cfg):
    opener = opener_returning('{"decision": "MAYBE", "n": 1}')
    r, fake = make_router(db, cfg, opener)
    text, *_ , label = r.json_call("gate", SYSTEM, "post", SCHEMA)
    assert len(opener.calls) == 2 and fake.calls == 1 and label == "claude-test"
    assert "invalid answer" in r.notes[0]


def test_retry_succeeds(db, cfg):
    opener = opener_returning("not json", '{"decision": "PASS", "n": 1}')
    r, fake = make_router(db, cfg, opener)
    assert r.json_call("gate", SYSTEM, "post", SCHEMA)[3] == "groq:m" and fake.calls == 0


def test_other_stages_and_missing_key_use_anthropic(db, cfg):
    r, fake = make_router(db, cfg, opener_returning("{}"))
    assert r.json_call("full", SYSTEM, "post", SCHEMA)[3] == "claude-test"
    r, fake = make_router(db, cfg, opener_returning("{}"), key=None)
    assert r.json_call("gate", SYSTEM, "post", SCHEMA)[3] == "claude-test"
    assert "no API key" in r.notes[0]


def test_daily_limit_falls_back(db, cfg):
    opener = opener_returning('{"decision": "PASS", "n": 1}')
    r, fake = make_router(db, cfg, opener)
    settings.put(db, {"ai_providers": [{"id": "groq", "base_url": "https://x/v1", "model": "m", "per_day": 1}]})
    r.confs = {p["id"]: p for p in settings.get(db, "ai_providers")}
    assert r.json_call("gate", SYSTEM, "post", SCHEMA)[3] == "groq:m"
    assert r.json_call("gate", SYSTEM, "post", SCHEMA)[3] == "claude-test"


def test_claude_code_provider_runs_the_cli_and_reads_structured_output():
    import json as _json
    from apartment_bot.providers import ClaudeCodeProvider

    class Done:
        returncode, stderr = 0, ""
        stdout = _json.dumps({"is_error": False, "structured_output": {"a": 1},
                              "usage": {"input_tokens": 10, "cache_creation_input_tokens": 90, "output_tokens": 5}})

    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return Done()

    p = ClaudeCodeProvider({"id": "claude-code", "model": "haiku"}, run=fake_run)
    text, usage, cost = p.call([{"type": "text", "text": "SYSTEM"}], "the question", {"type": "object"})
    assert _json.loads(text) == {"a": 1} and usage["input_tokens"] == 100 and cost == 0.0
    assert "--system-prompt-file" in seen["cmd"] and seen["kw"]["input"] == "the question"
    assert seen["kw"]["env"].get("ANTHROPIC_API_KEY") is None        # the subscription, not an API key
