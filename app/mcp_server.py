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


PUBLIC = os.environ.get("MILES_DEMO_PUBLIC", "http://localhost:8850").rstrip("/")


INSTRUCTIONS = (
    "Miles is a music producer agent. To make a track, use the step tools in order and narrate briefly between "
    "steps so the user can follow along: plan_beat → find_reference (optional, a YouTube track to learn from) → "
    "build_beat → check_beat → if a check fails, revise_beat with a concrete fix, then check_beat again (max 2 "
    "repairs). After each step, write one or two sentences quoting Miles's key decision (tempo/key chosen, sample "
    "picked, what passed or failed and why) and show the 'Open in the DAW' link for every take. make_music does the "
    "whole chain in one call when the user just wants the result.")


def notify(token, n: int, message: str):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/message",
                                 "params": {"level": "info", "logger": "miles", "data": message}}) + "\n")
    sys.stdout.flush()
    if token is None:
        return
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress",
                                 "params": {"progressToken": token, "progress": n, "message": message}}) + "\n")
    sys.stdout.flush()


def make_music(brief: str, use_cache: bool = False, token=None) -> dict:
    job = http("POST", "/api/make", {"text": brief, "live": not use_cache})
    jid = job["id"]
    notify(token, 0, f"Miles started. Watch it live in the DAW: {PUBLIC}/?job={jid}")
    seen, n, t0 = 0, 0, time.time()
    while True:
        time.sleep(1.5)
        j = http("GET", f"/api/jobs/{jid}")
        ev = j.get("events") or []
        for e in ev[seen:]:
            n += 1
            notify(token, n, f"{e.get('label')}: {e.get('text')}")
        seen = len(ev)
        if j["state"] != "running" or time.time() - t0 > 900:
            break
    if j["state"] != "done":
        return {"error": j.get("error") or "Miles is still working, open the DAW link", "daw": f"{PUBLIC}/?job={jid}",
                "events": ev}
    res, beat = j["result"], j.get("beat") or {}
    song = res.get("song") or {}
    path = str(HERE / "cache" / res["cached_audio"]) if res.get("cached") and res.get("cached_audio") else song.get("audio")
    daw = f"{PUBLIC}/?song={beat['song_id']}&v={beat['v']}" if beat.get("song_id") else f"{PUBLIC}/?job={jid}"
    return {"daw": daw, "live_log": f"{PUBLIC}/?job={jid}", "track_path": path,
            "track_url": PUBLIC + res["url"] if res.get("url") else None,
            "title": song.get("title"), "brief": {k: res.get("brief", {}).get(k) for k in ("bpm", "key", "mood", "length")},
            "pass": res.get("pass"), "checks": res.get("checks"),
            "measured": {k: (res.get("measured") or {}).get(k) for k in ("bpm", "key")},
            "build_seconds": res.get("seconds"), "why_failed": res.get("why") or [], "events": ev,
            "self_check_calls": len(res.get("verify") or []), "replayed_from_cache": bool(res.get("cached"))}


def pretty(o: dict) -> str:
    if o.get("error"):
        return f"Miles: {o['error']}\nOpen in the DAW: {o.get('daw')}"
    b, m, c = o.get("brief") or {}, o.get("measured") or {}, o.get("checks") or {}
    mark = lambda k: "✓" if c.get(k) else "✗" if c.get(k) is False else "·"
    bpm, want = m.get("bpm"), b.get("bpm")
    tempo = f"{bpm:.1f} BPM" if bpm else "—"
    if bpm and want and abs(bpm / want - 2) < 0.08:
        tempo = f"{bpm / 2:.1f} BPM (heard {bpm:.1f}, double-time)"
    steps = [e for e in o.get("events") or [] if e.get("kind") not in ("check",)]
    lines = [f"Open in the DAW: {o['daw']}", "",
             f"♪ {o.get('title') or 'track'}  ·  self-check {'PASS' if o.get('pass') else 'FAIL'}",
             f"  {mark('delivered')} Delivered  built in {o.get('build_seconds') or 0:.0f} s (bar 180 s)",
             f"  {mark('tempo')} Tempo      {tempo}" + (f"   asked {want:g} BPM" if want else ""),
             f"  {mark('key')} Key        {m.get('key') or '—'}   asked {b.get('key') or '—'}",
             f"  {mark('clean')} Clean      Miles's ear on the stems"]
    lines += [f"  why: {w}" for w in o.get("why_failed") or []]
    if steps:
        lines += ["", "What Miles did:"] + [f"  {i}. {e.get('label')}: {e.get('text')}" + (" ✗" if e.get("status") == "error" else "")
                                            for i, e in enumerate(steps[:20], 1)]
    if o.get("self_check_calls"):
        lines.append(f"  (Miles ran verify_songs on itself {o['self_check_calls']}× before delivering)")
    lines += ["", f"  file   {o.get('track_path')}", f"  play   {o.get('track_url')}"]
    if o.get("replayed_from_cache"):
        lines.append("  (replay of a real earlier run of this brief)")
    return "\n".join(lines)


ORDER = ("Natural order: plan_beat → find_reference (optional) → build_beat → check_beat → revise_beat if a check "
         "fails → check_beat again. make_music does all of it in one call.")
S = lambda **p: {"type": "object", "properties": p, "required": [k for k, v in p.items() if not v.pop("_opt", False)]}
STR = lambda d, opt=False: {"type": "string", "description": d, **({"_opt": True} if opt else {})}
NUM = lambda d, opt=True: {"type": "number", "description": d, **({"_opt": True} if opt else {})}
STEP_TOOLS = [
    {"name": "plan_beat", "description": "Step 1. Turn a brief into a spec Miles will be held to: BPM, key, mood, length, "
     "instruments, plus the genre's typical tempo/keys from real uploads. Returns plan_id for build_beat. " + ORDER,
     "inputSchema": S(brief=STR("what the music is for"))},
    {"name": "find_reference", "description": "Step 2 (optional). Search YouTube for a reference track, download a short "
     "audio clip (never played) and measure its BPM, key and loudness. " + ORDER,
     "inputSchema": S(query=STR("e.g. 'nujabes aruarian dance'"))},
    {"name": "build_beat", "description": "Step 3. Miles produces the track from a plan (plan_id) or a spec/brief; 1-4 "
     "minutes. Returns song_id, version and a DAW link. " + ORDER,
     "inputSchema": S(plan_id=STR("from plan_beat", True), brief=STR("if no plan_id", True),
                      reference=STR("optional reference (title or URL) to study", True))},
    {"name": "check_beat", "description": "Step 4. Miles's self-check, measured from the audio: Delivered, Tempo, Key, "
     "Clean vs what was asked. " + ORDER,
     "inputSchema": S(song_id=STR("s_…"), plan_id=STR("the plan it was built from", True),
                      brief=STR("or the brief", True))},
    {"name": "revise_beat", "description": "Step 5. Change an existing song by instruction (e.g. 'tempo is off, make it "
     "exactly 90 BPM', 'darker, add swing'); returns the new version, its checks and a DAW link. " + ORDER,
     "inputSchema": S(song_id=STR("s_…"), instruction=STR("what to change"))},
    {"name": "analyze_audio", "description": "Measure any audio: BPM, key, loudness, length. Takes a local path, a song "
     "id (s_…), a localhost track URL or a YouTube URL.", "inputSchema": S(path_or_url=STR("what to analyze"))},
    {"name": "list_beats", "description": "Recent beats with title, BPM, key, self-check results and DAW links.",
     "inputSchema": S(limit=NUM("how many (default 10)"))},
    {"name": "get_stems", "description": "Stem files (drums, bass, sample) of a song, with paths and play URLs.",
     "inputSchema": S(song_id=STR("s_…"), v=NUM("version (default latest)"))},
]


def run_step_tool(name: str, args: dict, token) -> str:
    sys.path.insert(0, str(HERE))
    import tools
    n = [0]
    try:
        jid = http("POST", "/api/jobs", {"tool": name, "text": f"{name}({json.dumps(args)[:200]})"})["id"]
        notify(token, 0, f"{name} started · live in the DAW: {PUBLIC}/?job={jid}")
    except Exception:                                               # noqa: BLE001
        jid = None

    def emit(label, text, detail="", status="done"):
        n[0] += 1
        notify(token, n[0], f"{label}: {text}")
        if jid:
            try:
                http("POST", f"/api/jobs/{jid}/event", {"label": label, "text": text, "detail": detail, "status": status})
            except Exception:                                       # noqa: BLE001
                pass
    try:
        r = tools.TOOLS[name](args, emit)
    except Exception as e:                                          # noqa: BLE001
        if jid:
            try:
                http("POST", f"/api/jobs/{jid}/done", {"error": str(e)[:300]})
            except Exception:                                       # noqa: BLE001
                pass
        return f"{name} failed: {e}"
    if jid:
        try:
            http("POST", f"/api/jobs/{jid}/done", {"song": {"song_id": r["song_id"], "v": r.get("v")} if r.get("song_id") else None})
        except Exception:                                           # noqa: BLE001
            pass
    return pretty_step(name, r) + (f"\nLive log: {PUBLIC}/?job={jid}" if jid else "")


def pretty_step(name: str, r: dict) -> str:
    L = []
    link = lambda d: f"{PUBLIC}{d}" if d else None
    mk = lambda v: "✓" if v else "✗" if v is False else "·"
    g = r.get("genre") or {}
    m0 = r.get("measured") or r
    say = {
        "plan_beat": lambda: f"Miles: I read the brief and I'm holding myself to {r['bpm']:g} BPM in {r['key']}"
                             + (f" — {g['name']} usually sits around {g['typical_bpm']:g} BPM" if g.get("typical_bpm") else "") + ".",
        "find_reference": lambda: f"Miles: I pulled “{r.get('title')}” from YouTube and listened: it's {m0.get('bpm')} BPM in {m0.get('key')}.",
        "analyze_audio": lambda: f"Miles: I listened to {Path(str(r.get('path'))).name}: {m0.get('bpm')} BPM, {m0.get('key')}, {m0.get('seconds')} s.",
        "build_beat": lambda: f"Miles: I built “{r['title']}” — {r.get('bpm') or '?'} BPM in {r.get('key') or '?'}, {r.get('seconds') or 0:.0f} s of work.",
        "check_beat": lambda: ("Miles: I measured it from the audio and it passes everything." if r["pass"] else
                               "Miles: I measured it from the audio and it fails: " + "; ".join(r.get("why") or []) + "."),
        "revise_beat": lambda: f"Miles: I revised it (v{r['from_v']} → v{r['v']}): “{r.get('instruction', '')[:90]}”. "
                               + ("Now it passes." if r["pass"] else "Still failing: " + "; ".join(r.get("why") or []) + "."),
        "list_beats": lambda: f"Miles: here are my {len(r['beats'])} latest beats.",
        "get_stems": lambda: f"Miles: here are the stems for {r['song_id']} v{r['v']}.",
    }.get(name)
    if say:
        L.append(say())
    if r.get("daw"):
        L.append(f"Open in the DAW: {link(r['daw'])}")
    if name == "plan_beat":
        g = r.get("genre") or {}
        L += [f"plan_id {r['plan_id']}", f"  tempo   {r['bpm']:g} BPM", f"  key     {r['key']}",
              f"  mood    {', '.join(r['mood']) or '—'}", f"  length  {r['length']} s",
              f"  parts   {', '.join(r['instruments'])}"]
        if g:
            L.append(f"  genre   {g['name']}: typical ~{g['typical_bpm']} BPM, keys {', '.join(g['common_keys'])}")
        L.append("Next: build_beat(plan_id=…)")
    elif name in ("find_reference", "analyze_audio"):
        m = r.get("measured") or r
        if r.get("title"):
            L += [f"♪ {r['title']}", f"  {r['url']}"]
        L += [f"  tempo     {m.get('bpm')} BPM", f"  key       {m.get('key')} (runner-up {m.get('key_runner_up')})",
              f"  loudness  {m.get('loudness_db_rms')} dB RMS, peak {m.get('peak_db')} dB",
              f"  length    {m.get('seconds')} s analysed", f"  file      {r.get('path')}"]
    elif name == "build_beat":
        L += [f"♪ {r['title']}  ·  song_id {r['song_id']} v{r['v']}  ·  built in {r.get('seconds') or 0:.0f} s"]
        if r.get("parts"):
            L += ["  parts:"] + [f"    {x}" for x in r["parts"]]
        if r.get("steps"):
            L += ["  what I did:"] + [f"    {i}. {x}" for i, x in enumerate(r["steps"][:12], 1)]
        L += [f"  play  {PUBLIC}{r['url']}", f"  file  {r['audio']}", "Next: check_beat(song_id=…, plan_id=…)"]
    elif name in ("check_beat", "revise_beat"):
        c, a, m = r["checks"], r["asked"], r["measured"]
        if name == "revise_beat":
            L.append(f"♪ {r.get('title')}  v{r['from_v']} → v{r['v']}")
            if r.get("steps"):
                L += ["  what I did:"] + [f"    {i}. {x}" for i, x in enumerate(r["steps"][:10], 1)]
        L += [f"Self-check {'PASS' if r['pass'] else 'FAIL'}  ({r['song_id']} v{r['v']})",
              f"  {mk(c.get('delivered'))} Delivered", f"  {mk(c.get('tempo'))} Tempo  {m.get('bpm')} BPM   asked {a.get('bpm')}",
              f"  {mk(c.get('key'))} Key    {m.get('key')}   asked {a.get('key')}", f"  {mk(c.get('clean'))} Clean"]
        L += [f"  why: {w}" for w in r.get("why") or []]
        if not r["pass"]:
            fix = []
            if c.get("tempo") is False and a.get("bpm"):
                fix.append(f"set the tempo to exactly {a['bpm']:g} BPM")
            if c.get("key") is False and a.get("key"):
                fix.append(f"move everything to {a['key']} (bass root on {a['key'].rstrip('m')})")
            L.append("Decision: " + (" and ".join(fix) or "fix what failed") +
                     " → revise_beat(song_id=…, instruction=…), then check_beat again")
    elif name == "list_beats":
        for i, b in enumerate(r["beats"], 1):
            c = b.get("checks") or {}
            L.append(f"{i:>2}. {b['title']}  ·  {b.get('bpm') and round(b['bpm'])} BPM {b.get('key') or ''}  ·  "
                     + " ".join(f"{k} {mk(c.get(k))}" for k in ("delivered", "tempo", "key", "clean"))
                     + (f"\n    {link(b['daw'])}" if b.get("daw") else f"\n    {PUBLIC}{b['url']}"))
    elif name == "get_stems":
        if r.get("note"):
            L.append(f"  ({r['note']})")
        L += [f"  {k:<7} {v['path']}\n          {PUBLIC}{v['url']}" for k, v in r["stems"].items()]
    return "\n".join(L)


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
                        "instructions": INSTRUCTIONS})
        elif method == "tools/list":
            reply(id_, {"tools": [TOOL] + STEP_TOOLS})
        elif method == "tools/call":
            p = m.get("params") or {}
            if p.get("name") in [t["name"] for t in STEP_TOOLS]:
                txt = run_step_tool(p["name"], p.get("arguments") or {}, (p.get("_meta") or {}).get("progressToken"))
                reply(id_, {"content": [{"type": "text", "text": txt}], "isError": txt.startswith(p["name"] + " failed")})
                continue
            if p.get("name") != "make_music":
                reply(id_, error={"code": -32602, "message": f"unknown tool {p.get('name')}"})
                continue
            a = p.get("arguments") or {}
            try:
                tok = ((p.get("_meta") or {}).get("progressToken"))
                out = make_music(str(a.get("brief") or ""), bool(a.get("use_cache")), tok)
                reply(id_, {"content": [{"type": "text", "text": pretty(out)}],
                            "isError": bool(out.get("error"))})
            except Exception as e:                                  # noqa: BLE001
                reply(id_, {"content": [{"type": "text", "text": f"error: {e!r}"[:500]}], "isError": True})
        elif method == "ping":
            reply(id_, {})
        else:
            reply(id_, error={"code": -32601, "message": f"no method {method}"})


if __name__ == "__main__":
    main()
