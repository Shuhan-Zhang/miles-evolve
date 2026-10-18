"""Miles for judges: one brief in, one checked track out. Shared by demo/server.py (the web UI) and demo/mcp_server.py.

A Miles studio (server/app.py) runs on MILES_DEMO_BACKEND_PORT (8851) with MILES_STUDIO_DATA=evolve/work/ui and
MILES_SELF_CHECK=1, so the producer runs verify_songs itself; evolve/check.py then measures the delivered audio.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evolve import contract, pool  # noqa: E402

PORT = int(os.environ.get("MILES_DEMO_BACKEND_PORT", 8851))
DATA = pool.WORK / "ui"
CACHE = Path(__file__).resolve().parent / "cache"
VERSION = ROOT / "evolve/versions/v0"
TRAILER = "Movie trailer cue, tense cinematic build, 90 BPM, C minor"

MOODS = ["tense", "cinematic", "dark", "uplifting", "epic", "warm", "playful", "dreamy", "aggressive", "chill",
         "hopeful", "sad", "nostalgic", "energetic", "triumphant", "mysterious", "romantic", "gritty", "bright"]


def brief(text: str) -> dict:
    """The spec card: tempo and key exactly as the checker will hold Miles to them, plus mood and length."""
    c = contract.contract(text)
    low = text.lower()
    moods = [m for m in MOODS if m in low][:3]
    secs = None
    m = re.search(r"(\d{1,3})\s*(s\b|sec|second)", low)
    if m and m.group(2) == "s" and m.group(1) in ("50", "60", "70", "80", "90"):   # "90s japanese piano" is a decade
        m = None
    if m:
        secs = int(m.group(1))
    elif re.search(r"\b(trailer|commercial|spot|ad)\b", low):
        secs = 30
    return {"bpm": c.get("bpm"), "key": c.get("key"), "style": c.get("style"), "mood": moods or ["—"],
            "length": secs, "text": text}


_worker: pool.Worker | None = None


def backend() -> pool.Worker:
    """The backing studio, started once (or reused if it's already serving on PORT)."""
    global _worker
    if _worker is None:
        w = pool.Worker(0)
        w.port, w.data = PORT, DATA
        book, flags = pool.version_files(VERSION)
        flags["MILES_SELF_CHECK"] = "1"
        w.book, w.flags = book, flags
        if not pool.up(PORT):
            w.start(book, flags)
            w.wait_up()
        _worker = w
    return _worker


def make(text: str, timeout: float = 420) -> dict:
    """Run one job on Miles and self-check it: {brief, track, seconds, checks, verify, reply}."""
    b = brief(text)
    w = backend()
    out = w.job(f"{text} — make 1 track like that", f"ui · {time.strftime('%H:%M:%S')}", timeout)
    task = {"brief": text, "stated_bpm": b["bpm"], "stated_key": b["key"], "title": text}
    test = pool.self_test(task, out) or {}
    best = test.get("best") or {}
    song = next((s for s in out.get("songs") or [] if s.get("audio") and best.get("song") in (None, s.get("id"))),
                None) or next((s for s in out.get("songs") or [] if s.get("audio")), None)
    return {"brief": b, "song": song, "seconds": out.get("seconds"), "error": out.get("error"),
            "reply": out.get("reply"), "verify": out.get("verify"),
            "checks": {k: best.get(k) for k in ("delivered", "tempo", "key", "clean")},
            "pass": bool(best.get("pass")), "measured": best.get("measured"), "why": best.get("why"),
            "made_at": time.strftime("%Y-%m-%d %H:%M:%S")}


def save_cache(name: str, res: dict) -> dict:
    """Copy the track next to its result so the demo can replay a real earlier run without the backend."""
    CACHE.mkdir(exist_ok=True)
    if res.get("song") and res["song"].get("audio") and Path(res["song"]["audio"]).exists():
        src = Path(res["song"]["audio"])
        dst = CACHE / f"{name}{src.suffix}"
        shutil.copyfile(src, dst)
        res["cached_audio"] = dst.name
    (CACHE / f"{name}.json").write_text(json.dumps(res, indent=1, default=str))
    return res


if __name__ == "__main__":
    r = save_cache(sys.argv[2] if len(sys.argv) > 2 else "trailer", make(sys.argv[1] if len(sys.argv) > 1 else TRAILER))
    print(json.dumps(r, indent=1, default=str))
