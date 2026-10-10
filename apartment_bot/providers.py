"""AI providers: Anthropic (as before) and any OpenAI-compatible chat API (Groq, OpenRouter,
Gemini's OpenAI endpoint, a local server, ...), chosen per analysis stage.

Settings (app_settings, edited in the app):
    ai_providers: [{"id", "label", "base_url", "model", "json_mode", "per_minute", "per_day",
                    "price_in", "price_out"}]       # json_mode: json_schema | json_object
    ai_stages:    {"gate": id, "triage": id, "full": id, "group_score": id}   # "anthropic" = default
    anthropic_limits: {"per_minute": n | null, "per_day": n | null}
API keys are NOT in the database (it gets copied and sent around): they live in
secrets/ai_keys.json as {provider id: key}; Anthropic's key comes from config.yaml as before.

An OpenAI-compatible answer is checked against the same JSON schema the Anthropic call is
forced into. An invalid answer is retried once; then (or when the provider's daily limit is
used up, or it fails) the request falls back to Anthropic.
"""
import json
import urllib.error
import urllib.request
from pathlib import Path

from . import settings
from .ratelimit import RateLimited, RateLimiter

SECRETS_DIR = Path(__file__).resolve().parent.parent / "secrets"
KEYS_FILE = SECRETS_DIR / "ai_keys.json"
STAGES = ("gate", "triage", "full", "group_score")
ANTHROPIC = "anthropic"

# Ready-made entries the app offers (free tiers change; check each provider's page).
PRESETS = [
    {"id": "groq", "label": "Groq", "base_url": "https://api.groq.com/openai/v1",
     "model": "llama-3.3-70b-versatile", "json_mode": "json_object", "per_minute": 30, "per_day": 1000},
    {"id": "openrouter", "label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
     "model": "meta-llama/llama-3.3-70b-instruct:free", "json_mode": "json_object", "per_minute": 20, "per_day": 50},
    {"id": "gemini", "label": "Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "model": "gemini-2.5-flash", "json_mode": "json_schema", "per_minute": 10, "per_day": 250},
]


class ProviderError(Exception):
    pass


class InvalidOutput(ProviderError):
    pass


# --- keys ---------------------------------------------------------------------------------

def load_keys():
    try:
        return json.loads(KEYS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_key(provider_id, key):
    SECRETS_DIR.mkdir(exist_ok=True)
    keys = load_keys()
    if key:
        keys[provider_id] = key.strip()
    else:
        keys.pop(provider_id, None)
    KEYS_FILE.write_text(json.dumps(keys, indent=1), encoding="utf-8")


# --- schema check (the subset of JSON Schema the analysis uses) ----------------------------

def validate(value, schema, path="$"):
    """Raise InvalidOutput if value doesn't match schema (type, enum, required, properties, items)."""
    t = schema.get("type")
    if "enum" in schema and value not in schema["enum"]:
        raise InvalidOutput(f"{path}: {value!r} not in {schema['enum']}")
    checks = {"object": dict, "array": list, "string": str, "boolean": bool}
    if t in checks and not isinstance(value, checks[t]):
        raise InvalidOutput(f"{path}: expected {t}")
    if t == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise InvalidOutput(f"{path}: expected integer")
    if t == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        raise InvalidOutput(f"{path}: expected number")
    if t == "object":
        for k in schema.get("required", []):
            if k not in value:
                raise InvalidOutput(f"{path}: missing {k}")
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                validate(value[k], sub, f"{path}.{k}")
    if t == "array" and "items" in schema:
        for i, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{i}]")


# --- providers ----------------------------------------------------------------------------

class AnthropicProvider:
    """The original path: structured outputs guarantee the schema."""
    id = ANTHROPIC

    def __init__(self, client, model):
        self.client, self.model = client, model
        self.label = model

    def call(self, system_blocks, user_content, schema, max_tokens=16000):
        from .ai_filter import AnalysisError, cost_usd
        response = self.client.messages.create(
            model=self.model, max_tokens=max_tokens, system=system_blocks,
            messages=[{"role": "user", "content": user_content}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        u = response.usage
        usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                 "cache_read_tokens": u.cache_read_input_tokens or 0,
                 "cache_write_tokens": u.cache_creation_input_tokens or 0}
        cost = cost_usd(self.model, usage)
        if response.stop_reason != "end_turn":
            raise AnalysisError(f"model stopped with stop_reason={response.stop_reason}", usage, cost)
        return next(b.text for b in response.content if b.type == "text"), usage, cost


class OpenAICompatProvider:
    """Any /chat/completions API. Prompt caching blocks are flattened into one system message."""

    def __init__(self, conf, api_key, opener=urllib.request.urlopen):
        self.conf, self.key, self.opener = conf, api_key, opener
        self.id = conf["id"]
        self.model = conf["model"]
        self.label = f"{conf['id']}:{conf['model']}"

    def call(self, system_blocks, user_content, schema, max_tokens=8000):
        system = "\n\n".join(b["text"] for b in system_blocks)
        body = {"model": self.model, "max_tokens": max_tokens, "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}]}
        if self.conf.get("json_mode", "json_object") == "json_schema":
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "answer", "strict": True, "schema": schema}}
        else:
            body["response_format"] = {"type": "json_object"}
            body["messages"][0]["content"] += ("\n\nAnswer with one JSON object only, matching this JSON schema "
                                               "exactly (all fields required):\n" + json.dumps(schema))
        req = urllib.request.Request(
            self.conf["base_url"].rstrip("/") + "/chat/completions", data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=120) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ProviderError(f"{self.id}: HTTP {e.code} {e.read()[:200]!r}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ProviderError(f"{self.id}: {e}")
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError(f"{self.id}: unexpected response {str(data)[:200]}")
        u = data.get("usage") or {}
        usage = {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0),
                 "cache_read_tokens": 0, "cache_write_tokens": 0}
        cost = (usage["input_tokens"] * float(self.conf.get("price_in") or 0)
                + usage["output_tokens"] * float(self.conf.get("price_out") or 0)) / 1_000_000
        return text, usage, cost


class Router:
    """Picks the provider for each stage, applies rate limits, validates, retries and falls back.

    Passed to ai_filter.analyze_post in place of an Anthropic client.
    """

    def __init__(self, db, cfg, anthropic_client_factory, keys=None, opener=urllib.request.urlopen,
                 sleep=None):
        self.db, self.cfg = db, cfg
        self._anthropic_factory = anthropic_client_factory
        self._anthropic = None
        s = settings.get_all(db)
        self.stages = {**{st: ANTHROPIC for st in STAGES}, **(s.get("ai_stages") or {})}
        self.confs = {p["id"]: p for p in s.get("ai_providers") or []}
        self.anthropic_limits = s.get("anthropic_limits") or {}
        self.keys = load_keys() if keys is None else keys
        self.opener = opener
        self.sleep = sleep
        self.notes = []          # fallbacks that happened, for the run summary

    def anthropic(self):
        if self._anthropic is None:
            self._anthropic = AnthropicProvider(self._anthropic_factory(), self.cfg["app"]["ai_model"])
        return self._anthropic

    def _limiter(self, provider_id, conf):
        kw = {"sleep": self.sleep} if self.sleep else {}
        return RateLimiter(self.db, provider_id, conf.get("per_minute"), conf.get("per_day"), **kw)

    def _provider(self, stage):
        pid = self.stages.get(stage, ANTHROPIC)
        if pid == ANTHROPIC or pid not in self.confs:
            return None
        if not self.keys.get(pid):
            self.notes.append(f"{pid}: no API key; used Anthropic")
            return None
        return OpenAICompatProvider(self.confs[pid], self.keys[pid], self.opener)

    def json_call(self, stage, system_blocks, user_content, schema):
        """(raw_text, usage, cost, label). raw_text is valid JSON for the schema."""
        primary = self._provider(stage)
        if primary:
            try:
                self._limiter(primary.id, self.confs[primary.id]).acquire()
                last = None
                for _ in range(2):   # one retry on an invalid answer
                    text, usage, cost = primary.call(system_blocks, user_content, schema)
                    try:
                        validate(json.loads(_strip_fences(text)), schema)
                        return _strip_fences(text), usage, cost, primary.label
                    except (ValueError, InvalidOutput) as e:
                        last = e
                self.notes.append(f"{primary.id}: invalid answer ({last}); used Anthropic")
            except RateLimited as e:
                self.notes.append(f"{e}; used Anthropic")
            except ProviderError as e:
                self.notes.append(f"{e}; used Anthropic")
        RateLimiter(self.db, ANTHROPIC, self.anthropic_limits.get("per_minute"),
                    self.anthropic_limits.get("per_day"), **({"sleep": self.sleep} if self.sleep else {})).acquire()
        a = self.anthropic()
        text, usage, cost = a.call(system_blocks, user_content, schema)
        return text, usage, cost, a.label


def _strip_fences(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()
