#!/usr/bin/env python3
"""Miles as one MCP tool, over stdio: make_music(brief) -> a track + Miles's own self-check of it.

    claude mcp add miles -- ~/logicagent/.venv/bin/python ~/miles-hackathon/demo/mcp_server.py

Calls the same backend as the web page (demo/server.py on MILES_DEMO_URL, default http://127.0.0.1:8850); if that
isn't running, runs the job in-process through demo/core.py. No SDK: newline-delimited JSON-RPC 2.0, as MCP stdio is.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
URL = os.environ.get("MILES_DEMO_URL", "http://127.0.0.1:8850").rstrip("/")

TOOL = {
    "name": "make_music",
    "description": ("Make an original music track from a brief (e.g. a commercial or trailer script with tempo, key, "
                    "mood, length). Miles produces it, then checks its own work from the audio: delivered on time, "
                    "tempo, key, clean. Returns the track's local path and URL plus each check with measured values. "
                    "A live render takes 1-4 minutes."),
    "inputSchema": {"type": "object", "properties": {
        "brief": {"type": "string", "description": "What the music is for, e.g. 'Movie trailer cue, tense cinematic "
                                                   "build, 90 BPM, C minor'"},
        "use_cache": {"type": "boolean", "description": "Return a real earlier run of the same brief if there is one "
                                                        "(instant). Default false: render now."}},
        "required": ["brief"]},
}


def http(method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
    req = urllib.request.Request(URL + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def make_music(brief: str, use_cache: bool = False) -> dict:
    try:
        job = http("POST", "/api/make", {"text": brief, "live": not use_cache})
        while True:
            time.sleep(1.5)
            j = http("GET", f"/api/jobs/{job['id']}")
            if j["state"] != "running":
                break
        if j["state"] == "error":
            return {"error": j.get("error")}
        res = j["result"]
        base = URL
    except OSError:                                                 # the page's server isn't up: run in-process
        sys.path.insert(0, str(HERE))
        import core
        res, base = core.make(brief), None
    song = res.get("song") or {}
    path = str(HERE / "cache" / res["cached_audio"]) if res.get("cached") and res.get("cached_audio") else song.get("audio")
    return {"track_path": path, "track_url": (base + res["url"]) if base and res.get("url") else None,
            "title": song.get("title"), "brief": {k: res.get("brief", {}).get(k) for k in ("bpm", "key", "mood", "length")},
            "pass": res.get("pass"), "checks": res.get("checks"),
            "measured": {k: (res.get("measured") or {}).get(k) for k in ("bpm", "key", "claimed_bpm", "claimed_key")},
            "build_seconds": res.get("seconds"), "why_failed": res.get("why") or [],
            "self_check_calls": len(res.get("verify") or []), "replayed_from_cache": bool(res.get("cached"))}


def reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    msg.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            m = json.loads(line)
        except ValueError:
            continue
        method, id_ = m.get("method"), m.get("id")
        if id_ is None:                                             # notifications (initialized, cancelled)
            continue
        if method == "initialize":
            reply(id_, {"protocolVersion": (m.get("params") or {}).get("protocolVersion", "2025-06-18"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "miles", "version": "0.1.0"},
                        "instructions": "Miles makes music. Call make_music with a brief; it returns a track and "
                                        "Miles's self-check of it (delivered, tempo, key, clean)."})
        elif method == "tools/list":
            reply(id_, {"tools": [TOOL]})
        elif method == "tools/call":
            p = m.get("params") or {}
            if p.get("name") != "make_music":
                reply(id_, error={"code": -32602, "message": f"unknown tool {p.get('name')}"})
                continue
            a = p.get("arguments") or {}
            try:
                out = make_music(str(a.get("brief") or ""), bool(a.get("use_cache")))
                reply(id_, {"content": [{"type": "text", "text": json.dumps(out, indent=1)}],
                            "structuredContent": out, "isError": bool(out.get("error"))})
            except Exception as e:                                  # noqa: BLE001
                reply(id_, {"content": [{"type": "text", "text": f"error: {e!r}"[:500]}], "isError": True})
        elif method == "ping":
            reply(id_, {})
        else:
            reply(id_, error={"code": -32601, "message": f"no method {method}"})


if __name__ == "__main__":
    main()
