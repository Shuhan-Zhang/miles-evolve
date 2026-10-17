#!/usr/bin/env python3
"""Collect real renders + their self-check results into demo/cache/gallery.json (audio copied to demo/cache/gallery/).

Sources: the demo's own trailer runs (demo/cache/trailer*.json) and passing takes from the self-improvement loop's runs
(evolve/runs/loop-20261017-141624-gN/gN/<brief>-s1.json, audio in evolve/work/wN/songs/<song>/).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "cache" / "gallery"
LOOP = ROOT / "evolve/runs"

# (source, display brief, mood, length)
PICKS = [
    ("cache:trailer_b", "Movie trailer cue, tense cinematic build, 90 BPM, C minor", ["Tense", "Cinematic"], 30),
    ("loop-20261017-141624-g4/g4/JGcwgSWBx0Y", "Late-night trapsoul R&B, 92 BPM, F minor", ["Moody", "Smooth"], 30),
    ("loop-20261017-141624-g3/g3/IKg3NrH6YmU", "Bright melodic trap for a sneaker drop, 125 BPM, D minor", ["Energetic", "Glossy"], 30),
    ("loop-20261017-141624-g4/g4/vLp4WP_-tfk", "90s boom bap, chill jazz x lofi for a coffee spot, 86 BPM, D minor", ["Warm", "Chill"], 30),
    ("loop-20261017-141624-g2/g2/w298TXv-KJk", "West-coast rap, hard and fast, 162 BPM, C minor", ["Aggressive", "Gritty"], 30),
    ("cache:trailer", "Movie trailer cue, tense cinematic build, 90 BPM, C minor", ["Tense", "Cinematic"], 30),
    ("loop-20261017-141624-g2/g2/zDy7zYJ1ZE8", "Dark minimal R&B, 93 BPM, C minor", ["Dark", "Spacious"], 30),
]


TITLES = {1: "You Got It · trapsoul", 2: "Pink · melodic trap", 3: "Refresh · jazz lofi", 4: "Turf · west-coast trap",
          6: "Trust Issues · minimal R&B"}


def from_cache(name: str) -> dict:
    r = json.loads((HERE / "cache" / f"{name}.json").read_text())
    return {"song_dir": str(Path(r["song"]["audio"]).parent), "audio": HERE / "cache" / r["cached_audio"], "title": r["song"]["title"], "style": r["song"].get("style"),
            "checks": r["checks"], "pass": r["pass"], "measured": r["measured"], "why": r.get("why") or [],
            "seconds": r["seconds"], "bpm": r["brief"]["bpm"], "key": r["brief"]["key"],
            "verify_calls": len(r.get("verify") or []), "source": f"demo run · {r.get('made_at', '')}",
            "claimed": {"bpm": r["song"].get("bpm"), "key": r["song"].get("key")}}


def from_loop(rel: str) -> dict:
    d = json.loads((LOOP / f"{rel}-s1.json").read_text())
    b = d["check"]["best"]
    sd = ROOT / "evolve/work" / d["worker"] / "songs" / b["song"]
    song = json.loads((sd / "song.json").read_text())
    vs = sorted(sd.glob("v*.wav"), key=lambda p: p.stat().st_mtime)
    mix = [p for p in vs if "_" not in p.stem][-1]
    c = d["check"]["contract"]
    return {"audio": mix, "title": song.get("title") or rel, "style": c.get("style"),
            "checks": {k: b.get(k) for k in ("delivered", "tempo", "key", "clean")}, "pass": bool(b.get("pass")),
            "measured": b.get("measured") or {}, "why": b.get("why") or [], "seconds": d.get("seconds"),
            "bpm": c.get("bpm"), "key": c.get("key"), "verify_calls": None,
            "source": f"self-improvement loop · round {d['version'].lstrip('g')}",
            "claimed": {"bpm": song.get("bpm"), "key": song.get("key")}}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    items = []
    for i, (src, brief, mood, length) in enumerate(PICKS):
        it = from_cache(src[6:]) if src.startswith("cache:") else from_loop(src)
        dst = OUT / f"{i:02d}{it['audio'].suffix}"
        shutil.copyfile(it["audio"], dst)
        stems = {}
        for role in ("drums", "bass", "sample"):
            src = it["audio"].parent / f"{it['audio'].stem}_{role}.wav"
            if not src.exists() and it.get("song_dir"):
                src = Path(it["song_dir"]) / f"v0_{role}.wav"
            if src.exists():
                shutil.copyfile(src, OUT / f"{i:02d}_{role}.wav")
                stems[role] = f"/cache/gallery/{i:02d}_{role}.wav"
        m = it["measured"]
        items.append({"id": i, "brief": brief, "mood": mood, "length": length, "bpm": it["bpm"], "key": it["key"],
                      "title": TITLES.get(i, it["title"]), "style": it["style"], "url": f"/cache/gallery/{dst.name}",
                      "checks": it["checks"], "pass": it["pass"], "why": it["why"], "seconds": it["seconds"],
                      "heard": {"bpm": m.get("bpm"), "key": m.get("key")}, "claimed": it["claimed"],
                      "verify_calls": it["verify_calls"], "source": it["source"], "stems": stems})
    (HERE / "cache" / "gallery.json").write_text(json.dumps(items, indent=1, default=str))
    print(f"{len(items)} beats -> {OUT}")


if __name__ == "__main__":
    main()
