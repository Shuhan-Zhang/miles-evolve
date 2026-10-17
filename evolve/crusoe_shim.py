"""Point the producer's agent loop at Crusoe Managed Inference (OpenAI-compatible chat/completions with tools).

Only active when CRUSOE_PRODUCER=1: server/producer.py then calls install(fallback), which swaps
engineer.fallback.chat (the agent turn) and engineer.fallback.ask_json (one forced-tool call) for Crusoe versions
that keep the exact return shapes. ~/logicagent itself is never edited; with the flag unset nothing here is imported.

    CRUSOE_PRODUCER=1                       turn it on (per worker process)
    CRUSOE_PRODUCER_MODEL=<id>              default nvidia/NVIDIA-Nemotron-3-Super-120B-A12B
    CRUSOE_PRODUCER_THINKING=0|1            chat_template_kwargs.enable_thinking (default 1)
    CRUSOE_PRODUCER_MAX_TOKENS=<n>          default 8000
    CRUSOE_PRODUCER_TIMEOUT=<s>             floor for one turn's HTTP budget (default 240; the producer asks for 60)
    CRUSOE_BASE_URL                         default https://api.inference.crusoecloud.com/v1
Key: evolve/llm.py _key() (CRUSOE_API_KEY, else .env.crusoe / ~/logicagent/.env). Never logged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time

import aiohttp

try:
    from evolve import llm
except ImportError:                                  # evolve/ on sys.path instead of the repo root
    import llm  # type: ignore

log = logging.getLogger("crusoe_shim")

DEFAULT_MODEL = "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B"
RETRY = (429, 500, 502, 503, 504)
STATS = {"calls": 0, "in": 0, "out": 0, "retries": 0, "seconds": 0.0}


def model() -> str:
    return os.environ.get("CRUSOE_PRODUCER_MODEL") or DEFAULT_MODEL


def url() -> str:
    return os.environ.get("CRUSOE_BASE_URL", "https://api.inference.crusoecloud.com/v1").rstrip("/") + "/chat/completions"


def _clean_history(messages: list[dict]) -> list[dict]:
    """Only the fields chat/completions takes (drops reasoning_content etc. that earlier turns may carry)."""
    out = []
    for m in messages:
        c = {"role": m["role"], "content": m.get("content")}
        if m.get("tool_calls"):
            c["tool_calls"] = [{"id": tc.get("id"), "type": "function",
                                "function": {"name": tc["function"]["name"],
                                             "arguments": tc["function"].get("arguments") or "{}"}}
                               for tc in m["tool_calls"]]
        if m["role"] == "tool":
            c["tool_call_id"] = m.get("tool_call_id")
            c["content"] = str(m.get("content") if m.get("content") is not None else "")
        elif c["content"] is None and m["role"] != "assistant":
            c["content"] = ""
        out.append(c)
    return out


def body(messages: list[dict], tools: list[dict], tool_choice="auto") -> dict:
    b = {"model": model(), "messages": _clean_history(messages),
         "max_tokens": int(os.environ.get("CRUSOE_PRODUCER_MAX_TOKENS") or 8000), "temperature": 0.6, "top_p": 0.95,
         "chat_template_kwargs": {"enable_thinking": os.environ.get("CRUSOE_PRODUCER_THINKING", "1") != "0"}}
    if tools:
        b["tools"] = tools
        b["tool_choice"] = tool_choice
    return b


def message(data: dict) -> dict:
    """choices[0].message in the shape fallback.chat returns: role, content (str|None), tool_calls only if any."""
    m = ((data.get("choices") or [{}])[0]).get("message") or {}
    text = re.sub(r"<think>.*?</think>", "", m.get("content") or "", flags=re.S).strip()
    calls = [{"id": tc.get("id") or f"call_{i}", "type": "function",
              "function": {"name": tc["function"]["name"], "arguments": tc["function"].get("arguments") or "{}"}}
             for i, tc in enumerate(m.get("tool_calls") or []) if (tc.get("function") or {}).get("name")]
    return {"role": "assistant", "content": text or None, **({"tool_calls": calls} if calls else {})}


async def _post(b: dict, timeout: float, retries: int = 6) -> dict:
    headers = {"Authorization": f"Bearer {llm._key()}", "Content-Type": "application/json"}
    t0 = time.perf_counter()
    last = ""
    for i in range(retries):
        left = timeout - (time.perf_counter() - t0)
        if left < 1:
            break
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=left)) as s:
                async with s.post(url(), headers=headers, json=b) as r:
                    try:
                        data = await r.json(content_type=None)
                    except (ValueError, aiohttp.ContentTypeError):
                        data = {"error": {"message": (await r.text())[:200]}}
                    status = r.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            status, data = 0, {"error": {"message": repr(e)[:200]}}
        if status == 200:
            u = data.get("usage") or {}
            STATS["calls"] += 1
            STATS["in"] += u.get("prompt_tokens", 0)
            STATS["out"] += u.get("completion_tokens", 0)
            STATS["seconds"] += time.perf_counter() - t0
            return data
        e = data.get("error")
        last = f"crusoe {b['model']} {status}: {(e.get('message') if isinstance(e, dict) else e) or data}"[:300]
        if status in RETRY or status == 0:
            STATS["retries"] += 1
            await asyncio.sleep(min(30, 2 ** i + random.random()))
            continue
        break
    raise RuntimeError(last or "crusoe: out of time")


async def chat(messages: list[dict], tools: list[dict], timeout: float = 8.0) -> tuple[dict, str]:
    """fallback.chat replacement: one agent turn -> (assistant message, model)."""
    if os.environ.get("ENGINEER_NO_LLM"):
        raise RuntimeError("no LLM")
    b = body(messages, tools)
    budget = max(timeout, float(os.environ.get("CRUSOE_PRODUCER_TIMEOUT") or 240))
    data = await _post(b, budget)
    msg = message(data)
    ch = (data.get("choices") or [{}])[0]
    if not msg.get("content") and not msg.get("tool_calls") and ch.get("finish_reason") == "length":
        b["max_tokens"] *= 2                                   # thinking ate the budget: once more, double
        msg = message(await _post(b, budget))
    return msg, b["model"]


async def ask_json(system: str, text: str, schema: dict, timeout: float = 6.0) -> tuple[dict, str] | None:
    """fallback.ask_json replacement: one forced function call -> (args, model), or None on failure."""
    if os.environ.get("ENGINEER_NO_LLM"):
        return None
    tool = {"type": "function", "function": {"name": "answer", "description": "Give the answer.", "parameters": schema}}
    b = body([{"role": "system", "content": system}, {"role": "user", "content": text}], [tool],
             tool_choice={"type": "function", "function": {"name": "answer"}})
    try:
        msg = message(await _post(b, max(timeout, 60.0), retries=3))
        for tc in msg.get("tool_calls") or []:
            return json.loads(tc["function"]["arguments"] or "{}"), b["model"]
        if msg.get("content"):
            return llm.first_json(msg["content"]), b["model"]
    except Exception as e:                                     # noqa: BLE001
        log.info("crusoe ask_json failed: %s", str(e)[:160])
    return None


def install(fallback) -> None:
    llm._key()                                                 # fail fast (SystemExit) if there is no key
    fallback.chat = chat
    fallback.ask_json = ask_json
    log.warning("producer LLM -> Crusoe %s (CRUSOE_PRODUCER=1)", model())
