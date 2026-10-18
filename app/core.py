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
DATA = Path(os.environ.get("MILES_DEMO_BACKEND_DATA") or pool.WORK / "ui")
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


# The pool: each build/revise takes a free studio. "port:datadir,…" overrides (tests use 8849).
BACKENDS = [(int(p_), Path(d)) for p_, d in (x.split(":", 1) for x in os.environ.get(
    "MILES_DEMO_BACKENDS", f"{PORT}:{DATA},8853:{pool.WORK / 'mcp1'},8854:{pool.WORK / 'mcp2'}").split(","))]
LOCKS = CACHE / "locks"


def song_dir(sid: str) -> Path | None:
    for _, d in BACKENDS:
        if (d / "songs" / sid).is_dir():
            return d / "songs" / sid
    return None


def port_of(sid: str) -> int:
    for p_, d in BACKENDS:
        if (d / "songs" / sid).is_dir():
            return p_
    return PORT


def busy_at(port: int) -> bool:
    try:
        return bool((pool.call(port, "GET", "/api/state", None, 5)[1] or {}).get("busy"))
    except Exception:                                          # noqa: BLE001
        return True                                            # down counts as busy


class Lease:
    """One studio, held with an flock (shared by the page server and every MCP server process)."""
    def __init__(self, port: int, data: Path, fh):
        self.port, self.data, self.fh = port, data, fh

    def release(self):
        import fcntl
        try:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()
        except OSError:
            pass


def acquire(note=None, only: int | None = None, limit: float = 900) -> Lease:
    import fcntl
    LOCKS.mkdir(parents=True, exist_ok=True)
    t0, said = time.time(), False
    while True:
        for port, data in BACKENDS:
            if only and port != only:
                continue
            fh = open(LOCKS / f"{port}.lock", "w")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                fh.close()
                continue
            if busy_at(port):                                  # e.g. a job started from the studio UI itself
                fcntl.flock(fh, fcntl.LOCK_UN)
                fh.close()
                continue
            return Lease(port, data, fh)
        if time.time() - t0 > limit:
            raise RuntimeError("every studio stayed busy for 15 min")
        if note and not said:
            note("Waiting for a studio", f"all {len(BACKENDS)} studios are busy; queued, starts as soon as one is free")
            said = True
        time.sleep(2)


def busy() -> bool:
    try:
        return bool((pool.call(PORT, "GET", "/api/state", None, 5)[1] or {}).get("busy"))
    except Exception:                                          # noqa: BLE001
        return False


def wait_free(note=None, limit: float = 600) -> None:
    """Another job holds the studio (409): wait for it instead of failing."""
    t0, said = time.time(), False
    while busy() and time.time() - t0 < limit:
        if note and not said:
            note("Waiting for the studio", "another job is running; queued, will start as soon as it's free")
            said = True
        time.sleep(3)


def job(text: str, name: str, timeout: float = 420, note=None, only: int | None = None) -> dict:
    """One job on a free studio from the pool (queued, never 409). only=port pins it (revisions of a song)."""
    for _ in range(40):
        lease = acquire(note, only)
        try:
            w = pool.Worker(0)
            w.port, w.data = lease.port, lease.data
            w.book = (lease.data / "MILES.md").read_text() if (lease.data / "MILES.md").exists() else ""
            if note:
                note("Studio", f"working on :{lease.port}")
            out = w.job(text, name, timeout)
        finally:
            lease.release()
        if not str(out.get("error") or "").startswith("HTTP 409"):
            return out | {"backend": lease.port}
        time.sleep(3)
    return out


def make(text: str, timeout: float = 420, note=None) -> dict:
    """Run one job on Miles and self-check it: {brief, track, seconds, checks, verify, reply}."""
    b = brief(text)
    out = job(f"{text} — make 1 track like that", f"ui · {time.strftime('%H:%M:%S')}", timeout, note)
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
