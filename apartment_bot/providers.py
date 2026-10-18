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
STAGES = ("gate", "triage", "full", "group_score", "meeting", "learning")
ANTHROPIC = "anthropic"

# Ready-made entries the app offers (free tiers change; check each provider's page).
PRESETS = [
    {"id": "groq", "label": "Groq", "base_url": "https://api.groq.com/openai/v1",
     "model": "llama-3.3-70b-versatile", "json_mode": "json_object", "per_minute": 30, "per_day": 1000},
    {"id": "openrouter", "label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
     "model": "meta-llama/llama-3.3-70b-instruct:free", "json_mode": "json_object", "per_minute": 20, "per_day": 50},
    {"id": "claude-code", "label": "Claude (my subscription, via Claude Code)", "kind": "claude_code",
     "base_url": "claude-code", "model": "haiku", "json_mode": "json_object", "per_minute": 6, "per_day": 400},
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


class ClaudeCodeProvider:
    """Claude through the official `claude` program on this PC, which is signed in to my Claude subscription:
    the questions count toward the plan's usage instead of costing API money. Slower (a few seconds a call)."""

    KIND = "claude_code"

    def __init__(self, conf, run=None):
        import subprocess
        self.conf = conf
        self.id = conf["id"]
        self.model = conf.get("model") or "haiku"
        self.label = f"{self.id}:{self.model}"
        self._run = run or subprocess.run

    def call(self, system_blocks, user_content, schema, max_tokens=8000):
        import os
        import shutil
        import tempfile
        exe = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude.exe")
        system = "\n\n".join(b["text"] for b in system_blocks)
        with tempfile.TemporaryDirectory() as tmp:           # an empty folder: no project files or memory are picked up
            sysfile = Path(tmp) / "system.txt"
            sysfile.write_text(system, encoding="utf-8")
            cmd = [exe, "-p", "--output-format", "json", "--json-schema", json.dumps(schema), "--tools", "",
                   "--no-session-persistence", "--disable-slash-commands", "--setting-sources", "",
                   "--system-prompt-file", str(sysfile), "--model", self.model]
            try:
                done = self._run(cmd, input=user_content, capture_output=True, text=True, encoding="utf-8",
                                 timeout=240, cwd=tmp, env={k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"})
            except (OSError, ValueError) as e:
                raise ProviderError(f"{self.id}: {e}")
            except Exception as e:                           # timeout
                raise ProviderError(f"{self.id}: {type(e).__name__}")
        try:
            data = json.loads(done.stdout)
        except ValueError:
            raise ProviderError(f"{self.id}: no JSON from claude (exit {done.returncode}): {(done.stderr or done.stdout)[:200]!r}")
        if data.get("is_error") or data.get("structured_output") is None:
            raise ProviderError(f"{self.id}: {str(data.get('result'))[:200]}")
        u = data.get("usage") or {}
        usage = {"input_tokens": (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0),
                 "output_tokens": u.get("output_tokens") or 0,
                 "cache_read_tokens": u.get("cache_read_input_tokens") or 0, "cache_write_tokens": 0}
        return json.dumps(data["structured_output"], ensure_ascii=False), usage, 0.0   # the subscription pays


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
        if self.confs[pid].get("kind") == ClaudeCodeProvider.KIND:
            return ClaudeCodeProvider(self.confs[pid])        # signed in on this PC: no API key
        if not self.keys.get(pid):
            self.notes.append(f"{pid}: no API key; used Anthropic")
            return None
        return OpenAICompatProvider(self.confs[pid], self.keys[pid], self.opener)

    def json_call(self, stage, system_blocks, user_content, schema, model=None):
        """(raw_text, usage, cost, label). raw_text is valid JSON for the schema.

        model: use this Anthropic model for the call (e.g. a stronger one for one stage) instead of
        the configured app.ai_model, and skip the other providers.
        """
        primary = None if model else self._provider(stage)
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
        a = AnthropicProvider(self.anthropic().client, model) if model else self.anthropic()
        text, usage, cost = a.call(system_blocks, user_content, schema)
        return text, usage, cost, a.label


def _strip_fences(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()
