#!/usr/bin/env python3
"""The judge-facing page for Miles: brief in, checked track out, and how Miles improved itself.

    ~/logicagent/.venv/bin/python demo/server.py            # http://localhost:8850

Backed by demo/core.py (a Miles studio on :8851 with MILES_SELF_CHECK=1). The prefilled trailer brief replays a real
earlier run from demo/cache/ unless "live" is set, so the demo can't stall on a slow render.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path

from aiohttp import web

import core

HERE = Path(__file__).resolve().parent
LEDGER = core.ROOT / "evolve/ledger.md"
LOOP = "loop-20261017-141624"
JOBS: dict[str, dict] = {}


def ledger() -> list[dict]:
    rows = []
    for line in LEDGER.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7 or cells[0] != LOOP:
            continue
        m = re.match(r"(\d+)/(\d+)\s*→\s*(\d+)/(\d+)", cells[4])
        before, after, total = (int(m.group(1)), int(m.group(3)), int(m.group(2))) if m else (None, None, None)
        rows.append({"gen": cells[1], "hypothesis": cells[2], "patch": cells[3], "before": before, "after": after,
                     "total": total, "delta": None if before is None else after - before, "noise": cells[5],
                     "kept": "✓" in cells[6], "ear": cells[7] if len(cells) > 7 else ""})
    return rows


def track_url(res: dict, name: str | None = None) -> str | None:
    if name and res.get("cached_audio"):
        return f"/cache/{res['cached_audio']}"
    a = (res.get("song") or {}).get("audio")
    if a and core.song_dir(Path(a).parent.name):
        return f"/track/{Path(a).parent.name}/{Path(a).name}"
    return None


def cached(name: str) -> dict | None:
    f = core.CACHE / f"{name}.json"
    if not f.exists():
        return None
    res = json.loads(f.read_text())
    res["url"], res["cached"] = track_url(res, name), True
    return res


async def api_brief(req):
    return web.json_response(core.brief(req.query.get("text", "")))


async def api_ledger(req):
    return web.json_response({"loop": LOOP, "rows": ledger()})


LIVE = core.CACHE / "live.json"            # beats made through this page or the MCP server (newest first)
JOBS_FILE = core.CACHE / "jobs.json"       # finished jobs, so ?job= links survive a restart


def load_json(f: Path, default):
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return default


JOBS.update(load_json(JOBS_FILE, {}))


def save_jobs():
    done = {k: v for k, v in JOBS.items() if v.get("state") != "running"}
    JOBS_FILE.write_text(json.dumps(dict(list(done.items())[-200:]), default=str))


def song_stems(audio: Path) -> dict:
    out = {}
    for role in ("drums", "bass", "sample"):
        f = audio.parent / f"{audio.stem}_{role}.wav"
        if f.exists():
            out[role] = f"/track/{audio.parent.name}/{f.name}"
    return out


async def track(req):
    """/track/<song>/<file>: a song's audio from whichever studio in the pool made it."""
    sid, name = req.match_info["sid"], req.match_info["name"]
    d = core.song_dir(sid) if re.fullmatch(r"s_[0-9a-f]+", sid) else None
    f = d / name if d else None
    if not f or not re.fullmatch(r"[\w.\-]+\.wav", name) or not f.exists():
        raise web.HTTPNotFound()
    return web.FileResponse(f)


def beat_of(res: dict, jid: str | None = None) -> dict | None:
    """A page 'beat' (what the browser and arrangement show) from a job result."""
    song = res.get("song") or {}
    if not res.get("url") or not song:
        return None
    b, m = res.get("brief") or {}, res.get("measured") or {}
    audio = Path(song["audio"]) if song.get("audio") else None
    v = int(re.sub(r"\D", "", audio.stem) or 0) if audio else 0
    return {"id": f"{song.get('id')}:{v}", "song_id": song.get("id"), "v": v, "job": jid, "brief": b.get("text") or "",
            "mood": [x.capitalize() for x in b.get("mood") or [] if x != "—"], "length": b.get("length"),
            "bpm": b.get("bpm"), "key": b.get("key"), "title": song.get("title") or "New track", "style": song.get("style"),
            "url": res["url"], "checks": res.get("checks"), "pass": res.get("pass"), "why": res.get("why") or [],
            "seconds": res.get("seconds"), "heard": {"bpm": m.get("bpm"), "key": m.get("key")},
            "claimed": {"bpm": song.get("bpm"), "key": song.get("key")}, "verify_calls": len(res.get("verify") or []),
            "source": ("replay of a real run · " if res.get("cached") else "live render · ") + str(res.get("made_at") or ""),
            "stems": {} if res.get("cached") or not audio else song_stems(audio)}


def fresh_stems(b: dict) -> dict:
    """Stems written after the beat was recorded (arranged songs, backfill) show up too."""
    if b.get("song_id") and not b.get("stems") and b.get("v") is not None:
        d = core.song_dir(b["song_id"])
        if d and (d / f"v{b['v']}.wav").exists():
            b["stems"] = song_stems(d / f"v{b['v']}.wav")
    return b


def remember(beat: dict):
    items = [x for x in load_json(LIVE, []) if x.get("id") != beat["id"]]
    LIVE.write_text(json.dumps([beat] + items[:49], default=str))


TOOL_WORDS = {"make_songs": "Building", "edit_songs": "Repairing", "verify_songs": "Self-check", "rank_songs": "Ranking",
              "pick": "Keeping", "study_reference": "Listening", "genre_profile": "Looking up the genre",
              "dig": "Digging records", "remember": "Noting for next time"}


def events_from(state: dict) -> list[dict]:
    """The producer's steps for the current job, in plain words."""
    ev = []
    plan = next((m.get("plan") for m in reversed(state.get("messages") or []) if m.get("role") == "agent" and m.get("plan")), None)
    for st in state.get("steps") or []:
        tool = st.get("tool") or ""
        ev.append({"kind": tool, "label": TOOL_WORDS.get(tool, tool.replace("_", " ").capitalize()),
                   "text": st.get("title") or "", "detail": (st.get("detail") or "")[:300], "status": st.get("status")})
    return {"events": ev, "plan": plan or [], "busy": state.get("busy_text") or ""}


async def watch(job: dict):
    """Mirror the backend's steps onto the job while it runs (the page and the MCP server poll /api/jobs/<id>)."""
    await asyncio.sleep(1.5)                                  # let the job open its own session first
    while job["state"] == "running":
        if not job.get("port"):
            await asyncio.sleep(1)
            continue
        try:
            _, st = await asyncio.to_thread(core.pool.call, job["port"], "GET", "/api/state", None, 5)
            pre = [e for e in job.get("events") or [] if e.get("kind") == "wait"]
            job.update(events_from(st or {}))
            job["events"] = pre + job["events"]
        except Exception:                                     # noqa: BLE001
            pass
        await asyncio.sleep(1)


def check_event(res: dict) -> dict:
    c, m, b = res.get("checks") or {}, res.get("measured") or {}, res.get("brief") or {}
    mk = lambda k: "✓" if c.get(k) else "✗" if c.get(k) is False else "·"
    bpm = m.get("bpm")
    if bpm and b.get("bpm") and abs(bpm / b["bpm"] - 2) < 0.08:
        bpm = bpm / 2
    parts = [f"delivered {res.get('seconds') or 0:.0f} s {mk('delivered')}",
             f"tempo {bpm or 0:.1f} (asked {b.get('bpm') or '—'}) {mk('tempo')}",
             f"key {m.get('key') or '—'} (asked {b.get('key') or '—'}) {mk('key')}", f"clean {mk('clean')}"]
    return {"kind": "check", "label": "Measured", "text": " · ".join(parts),
            "detail": "; ".join(res.get("why") or []), "status": "done" if res.get("pass") else "error"}


async def api_make(req):
    body = await req.json()
    text = str(body.get("text") or "").strip()
    if not text:
        raise web.HTTPBadRequest(text="text is required")
    jid = uuid.uuid4().hex[:8]
    job = JOBS[jid] = {"id": jid, "state": "running", "t0": time.time(), "text": text, "result": None}
    hit = None if body.get("live") else next((cached(n) for n in ("trailer_b", "trailer") if (cached(n) or {}).get("brief", {})
                                              .get("text", "").strip().lower() in text.lower()), None)
    if hit:
        job.update(replay=True, eta=6, events=[{"kind": "note", "label": "Replay", "status": "running",
                                                "text": "Replaying a real earlier run of this brief (tick live render for a new one)"}])

        async def replay():
            await asyncio.sleep(6)
            job["events"] = job["events"][:1] + [check_event(hit)]
            job["events"][0]["status"] = "done"
            b = beat_of(hit, jid)
            g = next((x for x in load_json(core.CACHE / "gallery.json", []) if x.get("seconds") == hit.get("seconds")), None)
            if b and g:
                b.update(stems=g.get("stems") or {}, url=g.get("url") or b["url"], title=g.get("title") or b["title"])
                remember(b)
            job.update(state="done", result=hit, beat=b)
            save_jobs()
        asyncio.create_task(replay())
    else:
        job.update(replay=False, eta=180)

        async def live():
            try:
                def note(label, text_):
                    if label == "Studio":
                        job["port"] = int(text_.rsplit(":", 1)[-1])
                    job.setdefault("events", []).append({"kind": "wait", "label": label, "text": text_,
                                                         "status": "done" if label == "Studio" else "running"})
                res = await asyncio.to_thread(core.make, text, 420, note)
                res["url"] = track_url(res)
                job["events"] = (job.get("events") or []) + [check_event(res)]
                b = beat_of(res, jid)
                if b:
                    remember(b)
                job.update(state="done" if b else "error", result=res, beat=b,
                           **({} if b else {"error": res.get("error") or "no track came back"}))
            except Exception as e:
                job.update(state="error", error=repr(e)[:300])
            save_jobs()
        job["events"] = []
        asyncio.create_task(live())
        asyncio.create_task(watch(job))
    return web.json_response({"id": jid, "replay": job["replay"], "daw": f"/?job={jid}"})


async def api_job_new(req):
    """Step tools (demo/tools.py via the MCP server) open a job here so the DAW's ?job= panel shows their steps."""
    body = await req.json()
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"id": jid, "state": "running", "t0": time.time(), "text": str(body.get("text") or "")[:300],
                 "tool": body.get("tool"), "events": [], "plan": [], "result": None, "replay": False, "eta": 120}
    return web.json_response({"id": jid, "daw": f"/?job={jid}"})


async def api_job_event(req):
    job = JOBS.get(req.match_info["id"])
    if not job:
        raise web.HTTPNotFound()
    e = await req.json()
    ev = job.setdefault("events", [])
    if ev and ev[-1].get("status") == "running":
        ev[-1]["status"] = "done"
    ev.append({k: str(e.get(k) or "")[:400] for k in ("label", "text", "detail", "status", "kind")})
    return web.json_response({"ok": True})


async def api_job_done(req):
    job = JOBS.get(req.match_info["id"])
    if not job:
        raise web.HTTPNotFound()
    body = await req.json()
    for e in job.get("events") or []:
        if e.get("status") == "running":
            e["status"] = "done"
    job.update(state="error" if body.get("error") else "done", error=body.get("error"), song=body.get("song"))
    save_jobs()
    return web.json_response({"ok": True})


async def api_job(req):
    job = JOBS.get(req.match_info["id"])
    if not job:
        raise web.HTTPNotFound()
    return web.json_response({k: v for k, v in job.items()} | {"elapsed": round(time.time() - job["t0"], 1)})


async def api_cached(req):
    return web.json_response(cached(req.query.get("name", "trailer")) or {})


async def api_gallery(req):
    f = core.CACHE / "gallery.json"
    live = [fresh_stems(b) for b in load_json(LIVE, [])]
    urls = {b.get("url") for b in live}
    return web.json_response(live + [b for b in (json.loads(f.read_text()) if f.exists() else []) if b.get("url") not in urls])


async def api_song(req):
    """?id=s_x&v=3 -> a beat for the arrangement (made here, or any song on the backend)."""
    sid, v = req.query.get("id", ""), req.query.get("v")
    for b in load_json(LIVE, []):
        if b.get("song_id") == sid and (v is None or str(b.get("v")) == str(v)):
            return web.json_response(fresh_stems(b))
    d = core.song_dir(sid) if re.fullmatch(r"s_[0-9a-f]+", sid) else None
    if not d:
        raise web.HTTPNotFound(text="no such song")
    wavs = sorted((p for p in d.glob("v*.wav") if "_" not in p.stem), key=lambda p: int(p.stem[1:] or 0))
    audio = next((p for p in wavs if v is not None and p.stem == f"v{v}"), wavs[-1] if wavs else None)
    if not audio:
        raise web.HTTPNotFound(text="no audio yet")
    meta = load_json(d / "song.json", {})
    from evolve import check
    r = await asyncio.to_thread(check.check, d, meta.get("brief") or "")
    m = r.get("measured") or {}
    c = r.get("contract") or {}
    return web.json_response({"id": f"{sid}:{audio.stem[1:]}", "song_id": sid, "v": int(audio.stem[1:] or 0),
                              "brief": meta.get("brief") or meta.get("title") or "", "mood": [], "length": None,
                              "bpm": c.get("bpm") or meta.get("bpm"), "key": c.get("key") or meta.get("key"),
                              "title": meta.get("title") or sid, "style": meta.get("style"),
                              "url": "/track/" + f"{sid}/{audio.name}",
                              "checks": {k: r.get(k) for k in ("delivered", "tempo", "key", "clean")},
                              "pass": r.get("pass"), "why": r.get("why") or [], "seconds": None,
                              "heard": {"bpm": m.get("bpm"), "key": m.get("key")}, "verify_calls": None,
                              "source": "backend song", "stems": song_stems(audio)})


async def index(req):
    return web.FileResponse(HERE / os.environ.get("MILES_DEMO_INDEX", "index.html"), headers={"Cache-Control": "no-store"})


def make_app():
    app = web.Application(client_max_size=1 << 30)
    app.add_routes([web.get("/", index), web.get("/api/brief", api_brief), web.get("/api/ledger", api_ledger),
                    web.post("/api/make", api_make), web.post("/api/jobs", api_job_new),
                    web.post("/api/jobs/{id}/event", api_job_event), web.post("/api/jobs/{id}/done", api_job_done), web.get("/api/jobs/{id}", api_job),
                    web.get("/api/cached", api_cached), web.get("/api/gallery", api_gallery), web.get("/api/song", api_song)])
    core.CACHE.mkdir(exist_ok=True)
    (core.DATA / "songs").mkdir(parents=True, exist_ok=True)
    app.router.add_static("/cache", core.CACHE)
    app.router.add_get("/track/{sid}/{name}", track)
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8850)
    a = ap.parse_args()
    if a.port == 8820:
        raise SystemExit("8820 is prod")
    web.run_app(make_app(), host="127.0.0.1", port=a.port)
