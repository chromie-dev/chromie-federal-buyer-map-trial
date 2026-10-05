"""One structured call to the model, recorded so it never has to be made twice.

An agent's judgment here is a JSON document that matches a schema. The request (model, prompts,
schema) is hashed; the answer is kept under that hash in research/cassettes, so a rebuild replays
the same judgment byte for byte, the checks stage runs with no key and no cost, and a live
second call on the same document (`--verify` in the callers) shows whether the judgment holds.
Keys come from the environment, else from the local env files, and are never printed.

Two ways to make the live call. With OPENAI_API_KEY the request goes to OpenAI's responses API
with a strict JSON schema, as every cassette before 2026-09-25 was made. Without it, on a machine
with the Claude Code command line (`claude`), the same prompts and schema go to `claude -p` with
`--json-schema`, tools off, and the answer is recorded under the Claude model's key. A replay looks
for the requested model's cassette first, then the Claude model's, so a layer read with one is read
back with either. LLM_PROVIDER=openai|claude forces one.

    python research/tools/llm.py --selfcheck
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASSETTES = ROOT / "research" / "cassettes"
ENV_FILES = (ROOT / ".env", Path.home() / "chromie" / ".env.local", Path.home() / "chromie" / ".env.development.local")
MODEL = "gpt-5.4-mini"
# Opus: on the same Air Force audit, Sonnet 5 at any effort composed the "exactly as the text writes it" fields
# (0 of 7 events kept by the verbatim linter) where Opus 5.5 copied them (5 of 5 kept), 2026-09-25.
CLAUDE_MODEL = os.environ.get("LLM_CLAUDE_MODEL", "").strip() or "claude-opus-5-5"
# Every Claude model a cassette may have been recorded under: a replay looks at each, so a reading made with one model
# (a stage run with LLM_CLAUDE_MODEL=claude-sonnet-5 for its volume) replays whatever the environment says today.
CLAUDE_MODELS = tuple(dict.fromkeys([CLAUDE_MODEL, "claude-opus-5-5", "claude-sonnet-5"]))
ENDPOINT = "https://api.openai.com/v1/responses"


def env_value(name: str) -> str:
    """The variable from the environment, else the first local env file that sets it; empty when none does."""
    if os.environ.get(name):
        return os.environ[name].strip()
    for path in ENV_FILES:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def request_key(model: str, system: str, user: str, schema: dict) -> str:
    return hashlib.sha256(json.dumps({"model": model, "system": system, "user": user, "schema": schema},
                                     sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def provider() -> str:
    """Who makes a live call: LLM_PROVIDER when set; else OpenAI when its key is present; else, for a profile other than
    the Navy's, the Claude command line when it is installed; else OpenAI, whose missing key names the gap. The Navy
    layer without a key keeps recording a record it has no cassette for as unread (research/docs/19, section 8) rather
    than reading it with another model."""
    forced = env_value("LLM_PROVIDER").lower()
    if forced in ("openai", "claude"):
        return forced
    if env_value("OPENAI_API_KEY"):
        return "openai"
    another_agency = (os.environ.get("AGENCY") or "navy").strip().lower() != "navy"
    return "claude" if another_agency and shutil.which("claude") else "openai"


def live_model(model: str, who: str) -> str:
    """The model a live call goes to: the one asked for, unless the Claude command line answers and the request named
    an OpenAI model."""
    return CLAUDE_MODEL if who == "claude" and not model.startswith("claude") else model


def claude_call(system: str, user: str, schema: dict, model: str) -> tuple[str, dict, str]:
    """One `claude -p` call with the schema enforced and no tools; the answer text, the usage and the model that answered."""
    # The reading is the model's judgment against a verbatim-evidence linter; LLM_EFFORT (low, medium, high) sets how
    # hard it thinks, medium by default, and is not part of the cassette key.
    cmd = ["claude", "-p", "--bare", "--output-format", "json", "--model", model, "--tools", "", "--no-session-persistence",
           "--effort", env_value("LLM_EFFORT") or "medium", "--json-schema", json.dumps(schema), "--system-prompt", system]
    for attempt in range(3):
        done = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        try:
            answer = json.loads(done.stdout)
        except json.JSONDecodeError:
            answer = {}
        if done.returncode == 0 and not answer.get("is_error") and "structured_output" in answer:
            usage = {"input_tokens": (answer.get("usage") or {}).get("input_tokens"), "output_tokens": (answer.get("usage") or {}).get("output_tokens")}
            used = next(iter(answer.get("modelUsage") or {}), model)
            return json.dumps(answer["structured_output"], ensure_ascii=False), usage, used
        if answer.get("stop_reason") == "refusal":
            # The model declined to read this document (a weapons notice, say). No retry would change it; the caller
            # records the document as unread with this reason, the way it records a missing key.
            raise LookupError(f"the model ({model}) refused to read this document; it stands unread")
        if attempt < 2:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"claude call failed: exit {done.returncode} {(done.stderr or done.stdout)[:300]!r}")


def structured(system: str, user: str, schema: dict, name: str, model: str = MODEL, *,
               cassettes: Path = CASSETTES, replay_only: bool = False, fresh: bool = False) -> tuple[dict, dict]:
    """The model's answer as a dict, and how it was had: `{"cassette", "replayed", "model", "usage"}`.
    `replay_only` refuses the network (the checks stage); `fresh` refuses the cassette (a verify run)."""
    who = provider()
    target = live_model(model, who)
    key = request_key(model, system, user, schema)
    path = cassettes / f"{key[:16]}.json"
    # The requested model's cassette first, then the live model's; under the Claude provider also the ones another
    # Claude model would have left, so a layer read on one machine replays on the other. A Navy build (OpenAI) never
    # replays a Claude cassette.
    also = [target] if target != model else []
    if who == "claude":
        also += [m for m in CLAUDE_MODELS if m != model and m not in also]
    for candidate in dict.fromkeys([key] + [request_key(m, system, user, schema) for m in also]):
        found = cassettes / f"{candidate[:16]}.json"
        if found.exists() and not fresh:
            saved = json.loads(found.read_text(encoding="utf-8"))
            return json.loads(saved["output_text"]), {"cassette": found.name, "replayed": True, "model": saved["model"], "usage": saved["usage"]}
    if replay_only:
        raise LookupError(f"no cassette for this request ({path.name}); run without --check to make the call")
    if who == "claude":
        key = request_key(target, system, user, schema)
        path = cassettes / f"{key[:16]}.json"
        text, usage, used = claude_call(system, user, schema, target)
        record = {"request_key": key, "model": used, "requested_model": target, "name": name, "provider": "claude",
                  "called_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "usage": usage,
                  "system_sha256": hashlib.sha256(system.encode()).hexdigest()[:16],
                  "user_sha256": hashlib.sha256(user.encode()).hexdigest()[:16], "output_text": text}
        if not fresh:
            cassettes.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return json.loads(text), {"cassette": path.name, "replayed": False, "model": used, "usage": usage}
    api_key = env_value("OPENAI_API_KEY")
    if not api_key:
        raise LookupError("OPENAI_API_KEY is not set in the environment or the local env files")
    body = {"model": model, "reasoning": {"effort": "low"},
            "input": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "text": {"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}}}
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode("utf-8"),
                                 headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    # A name lookup or a throttle fails a whole run otherwise; three tries, then the error as it came.
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                answer = json.loads(resp.read())
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"model call failed: HTTP {exc.code} {exc.read()[:300]!r}") from None
        except (urllib.error.URLError, TimeoutError):  # a read that stalls past the timeout is retried like a lookup
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    text = next(c["text"] for o in answer.get("output", []) if o.get("type") == "message"
                for c in o.get("content", []) if c.get("type") == "output_text")
    usage = {k: answer.get("usage", {}).get(k) for k in ("input_tokens", "output_tokens")}
    record = {"request_key": key, "model": answer.get("model", model), "requested_model": model, "name": name,
              "called_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "usage": usage,
              "system_sha256": hashlib.sha256(system.encode()).hexdigest()[:16],
              "user_sha256": hashlib.sha256(user.encode()).hexdigest()[:16], "output_text": text}
    if not fresh:
        cassettes.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return json.loads(text), {"cassette": path.name, "replayed": False, "model": record["model"], "usage": usage}


def selfcheck() -> int:
    import tempfile
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False}
    k1, k2 = request_key("m", "s", "u", schema), request_key("m", "s", "u2", schema)
    assert k1 != k2 and k1 == request_key("m", "s", "u", schema)
    with tempfile.TemporaryDirectory() as tmp:
        box = Path(tmp)
        try:
            structured("s", "u", schema, "t", model="m", cassettes=box, replay_only=True)
            raise AssertionError("replay-only must refuse the network")
        except LookupError:
            pass
        (box / f"{k1[:16]}.json").write_text(json.dumps({"output_text": '{"a": "x"}', "model": "m-2026", "usage": {"input_tokens": 1, "output_tokens": 1}}))
        out, meta = structured("s", "u", schema, "t", model="m", cassettes=box, replay_only=True)
        assert out == {"a": "x"} and meta["replayed"] and meta["model"] == "m-2026", (out, meta)
        assert env_value("THIS_VARIABLE_IS_NOT_SET_ANYWHERE_X") == ""
        os.environ["LLM_SELFCHECK_PROBE"] = " v "
        assert env_value("LLM_SELFCHECK_PROBE") == "v"
        # A cassette left by a Claude call replays for a request that named the OpenAI model, and the reverse.
        k3 = request_key(CLAUDE_MODEL, "s", "u3", schema)
        (box / f"{k3[:16]}.json").write_text(json.dumps({"output_text": '{"a": "y"}', "model": CLAUDE_MODEL, "usage": {}}))
        os.environ["LLM_PROVIDER"] = "claude"
        out, meta = structured("s", "u3", schema, "t", model=MODEL, cassettes=box, replay_only=True)
        assert out == {"a": "y"} and meta["model"] == CLAUDE_MODEL, (out, meta)
        assert live_model(MODEL, "claude") == CLAUDE_MODEL and live_model(MODEL, "openai") == MODEL and live_model("claude-opus-5-5", "claude") == "claude-opus-5-5"
        os.environ["LLM_PROVIDER"] = "openai"
        assert provider() == "openai"
        del os.environ["LLM_PROVIDER"]
    print("llm selfcheck ok")
    return 0


if __name__ == "__main__":
    sys.exit(selfcheck() if "--selfcheck" in sys.argv[1:] else print(__doc__) or 2)
