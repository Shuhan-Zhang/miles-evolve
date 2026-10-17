#!/usr/bin/env python3
"""Crusoe Managed Inference client (OpenAI-compatible), stdlib only, with backoff and a running $ tally.

    from evolve import llm
    text = llm.chat([{"role": "user", "content": "hi"}])
    obj = llm.chat_json(messages)                       # parses the first JSON object in the answer

    ~/logicagent/.venv/bin/python evolve/llm.py models  # GET /v1/models: ids and supported_parameters
    ~/logicagent/.venv/bin/python evolve/llm.py ping    # one tiny call to the coach model

Key from CRUSOE_API_KEY, else .env.crusoe / ~/logicagent/.env (never committed, never printed). Base URL from CRUSOE_BASE_URL. Coach model from
EVOLVE_COACH_MODEL (default Nemotron 3 Super). EVOLVE_FAKE_LLM=1 returns canned answers so the loop runs offline.
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("CRUSOE_BASE_URL", "https://api.inference.crusoecloud.com/v1").rstrip("/")
MODEL = os.environ.get("EVOLVE_COACH_MODEL", "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B")
PRICES = {                                         # USD per 1M tokens (input, output), Crusoe pricing page
    "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B": (0.30, 2.40),
    "nvidia/Nemotron-3.5-Lightning-30B-A3B": (0.05, 0.20),
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.05, 0.20),
    "deepseek-ai/DeepSeek-V4-Pro": (1.00, 3.00),
}
SPENT = {"calls": 0, "in": 0, "out": 0, "usd": 0.0}


def _from_files() -> str:
    """CRUSOE_API_KEY from <repo>/.env.crusoe or ~/logicagent/.env when it isn't exported."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for f in (root / ".env.crusoe", root / ".env", Path.home() / "logicagent/.env"):
        try:
            for line in f.read_text().splitlines():
                line = line.strip().removeprefix("export ").strip()
                if line.startswith("CRUSOE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
        except OSError:
            continue
    return ""


def _key() -> str:
    k = os.environ.get("CRUSOE_API_KEY", "").strip() or _from_files()
    if not k:
        raise SystemExit("CRUSOE_API_KEY is not set (export it in the shell that runs the loop; never commit it)")
    return k


def _req(method: str, path: str, body: dict | None = None, timeout: float = 300) -> dict:
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def models() -> list[dict]:
    return _req("GET", "/models").get("data", [])


def chat(messages: list[dict], model: str | None = None, max_tokens: int = 8000, temperature: float = 1.0,
         thinking: bool = True, retries: int = 6, **extra) -> str:
    """The answer text. Retries 429/5xx/timeouts with backoff; a too-small max_tokens (empty content) is retried
    once with double the budget."""
    model = model or MODEL
    if os.environ.get("EVOLVE_FAKE_LLM"):
        return _fake(messages)
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
            "top_p": 0.95, "chat_template_kwargs": {"enable_thinking": thinking}, **extra}
    for i in range(retries):
        try:
            r = _req("POST", "/chat/completions", body)
        except urllib.error.HTTPError as e:
            msg = e.read()[:300].decode(errors="replace")
            if e.code in (429, 500, 502, 503, 504) and i < retries - 1:
                time.sleep(min(60, 2 ** i + random.random()))
                continue
            raise RuntimeError(f"crusoe {e.code}: {msg}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if i < retries - 1:
                time.sleep(min(60, 2 ** i + random.random()))
                continue
            raise RuntimeError(f"crusoe unreachable: {e}") from None
        u = r.get("usage") or {}
        p_in, p_out = PRICES.get(model, (0.30, 2.40))
        SPENT["calls"] += 1
        SPENT["in"] += u.get("prompt_tokens", 0)
        SPENT["out"] += u.get("completion_tokens", 0)
        SPENT["usd"] += (u.get("prompt_tokens", 0) * p_in + u.get("completion_tokens", 0) * p_out) / 1e6
        ch = r["choices"][0]
        text = (ch["message"].get("content") or "").strip()
        if text:
            return text
        if ch.get("finish_reason") == "length":
            body["max_tokens"] = body["max_tokens"] * 2
            continue
        raise RuntimeError(f"crusoe: empty answer (finish_reason {ch.get('finish_reason')})")
    raise RuntimeError("crusoe: gave up after retries")


def first_json(text: str) -> dict:
    """The first balanced {...} in `text` (models wrap JSON in prose or ``` fences)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    start = text.find("{")
    while start != -1:
        depth, q, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if q:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    q = False
                continue
            if c == '"':
                q = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object in the answer")


def chat_json(messages: list[dict], **kw) -> dict:
    text = chat(messages, **kw)
    try:
        return first_json(text)
    except ValueError:
        fix = messages + [{"role": "assistant", "content": text},
                          {"role": "user", "content": "Answer again with only the JSON object, nothing else."}]
        return first_json(chat(fix, **kw))


_FAKES = [
    {"hypothesis": "the bass is written before the sample's key is known, so it clashes",
     "kind": "addendum", "text": "Before writing any bass, read the sample's detected key and write the bass in it."},
    {"hypothesis": "Miles says done without checking its songs against the brief",
     "kind": "flag", "flag": "MILES_SELF_CHECK", "value": "1"},
    {"hypothesis": "tempo drifts to the cookbook default instead of the brief's BPM",
     "kind": "playbook", "text": "The BPM in the brief is a hard requirement; set it before anything else."},
]


def _fake(messages) -> str:
    n = sum(m["role"] == "user" for m in messages)
    seed = sum(len(m["content"]) for m in messages)
    return "Here is my patch:\n```json\n" + json.dumps(_FAKES[(seed + n) % len(_FAKES)]) + "\n```"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ping"
    if cmd == "models":
        for m in models():
            print(m.get("id"), "|", ",".join(m.get("supported_parameters") or [])[:160])
    else:
        print(chat([{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=2000))
        print(json.dumps(SPENT))
