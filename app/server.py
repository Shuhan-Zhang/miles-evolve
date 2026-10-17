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
    if a and Path(a).resolve().is_relative_to((core.DATA / "songs").resolve()):
        return "/track/" + str(Path(a).resolve().relative_to((core.DATA / "songs").resolve()))
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


async def api_make(req):
    body = await req.json()
    text = str(body.get("text") or "").strip()
    if not text:
        raise web.HTTPBadRequest(text="text is required")
    jid = uuid.uuid4().hex[:8]
    job = JOBS[jid] = {"id": jid, "state": "running", "t0": time.time(), "text": text, "result": None}
    hit = None if body.get("live") else next((cached(n) for n in ("trailer_b", "trailer") if (cached(n) or {}).get("brief", {})
                                              .get("text", "").strip().lower() == text.lower()), None)
    if hit:
        job.update(replay=True, eta=6)

        async def replay():
            await asyncio.sleep(6)
            job.update(state="done", result=hit)
        asyncio.create_task(replay())
    else:
        job.update(replay=False, eta=180)

        async def live():
            try:
                res = await asyncio.to_thread(core.make, text)
                res["url"] = track_url(res)
                job.update(state="done", result=res)
            except Exception as e:
                job.update(state="error", error=repr(e)[:300])
        asyncio.create_task(live())
    return web.json_response({"id": jid, "replay": job["replay"]})


async def api_job(req):
    job = JOBS.get(req.match_info["id"])
    if not job:
        raise web.HTTPNotFound()
    return web.json_response({k: v for k, v in job.items()} | {"elapsed": round(time.time() - job["t0"], 1)})


async def api_cached(req):
    return web.json_response(cached(req.query.get("name", "trailer")) or {})


async def api_gallery(req):
    f = core.CACHE / "gallery.json"
    return web.json_response(json.loads(f.read_text()) if f.exists() else [])


async def index(req):
    return web.FileResponse(HERE / "index.html", headers={"Cache-Control": "no-store"})


def make_app():
    app = web.Application(client_max_size=1 << 30)
    app.add_routes([web.get("/", index), web.get("/api/brief", api_brief), web.get("/api/ledger", api_ledger),
                    web.post("/api/make", api_make), web.get("/api/jobs/{id}", api_job),
                    web.get("/api/cached", api_cached), web.get("/api/gallery", api_gallery)])
    core.CACHE.mkdir(exist_ok=True)
    (core.DATA / "songs").mkdir(parents=True, exist_ok=True)
    app.router.add_static("/cache", core.CACHE)
    app.router.add_static("/track", core.DATA / "songs")
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8850)
    a = ap.parse_args()
    if a.port == 8820:
        raise SystemExit("8820 is prod")
    web.run_app(make_app(), host="127.0.0.1", port=a.port)
