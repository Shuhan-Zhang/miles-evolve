#!/usr/bin/env python3
"""Write exact per-role stems for arranged versions made before engine._arrange saved them.
Re-renders each arrangement from its loop (deterministic) and writes stems only if the re-render matches the saved mix.
    MILES_STUDIO_DATA=<dir> ~/logicagent/.venv/bin/python demo/backfill_stems.py [song_id ...]
"""
import json, os, sys, types
from pathlib import Path
import numpy as np
import soundfile as sf
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import engine  # noqa: E402

ids = sys.argv[1:] or [p.name for p in sorted(engine.SONGS.glob("s_*"), key=lambda p: p.stat().st_mtime)[-30:]]
for sid in ids:
    d = engine.SONGS / sid
    meta = json.loads((d / "song.json").read_text()) if (d / "song.json").exists() else {}
    vs = meta.get("versions") or []
    for v in vs:
        if not v.get("arrangement") or (d / f"v{v['n']}_drums.wav").exists() or not (d / f"v{v['n']}.wav").exists():
            continue
        src = next((x for x in reversed(vs[: v["n"]]) if not x.get("arrangement") and x.get("stems")), None)
        if not src:
            continue
        fake = types.SimpleNamespace(id=sid, SECTIONS=engine.Song.SECTIONS, versions=vs,
                                     describe=lambda m=meta: {"bpm": m.get("bpm") or (m.get("state") or {}).get("bpm")})
        structure = [s["name"] for s in v.get("sections") or []] or None
        ok = False
        for bars in (8, 4, 16, 2):
            r = engine.Song._arrange(fake, src, structure or ["intro", "verse", "hook", "verse", "break", "hook", "outro"],
                                     bars, write=False)
            saved, _ = sf.read(str(d / f"v{v['n']}.wav"), always_2d=True)
            if r["parts"] and len(r["mix"]) == len(saved) and float(np.abs(r["mix"] - saved).max()) < 1e-3:
                for role, a in r["parts"].items():
                    sf.write(str(d / f"v{v['n']}_{role}.wav"), a, r["sr"])
                print(f"{sid} v{v['n']}: stems {sorted(r['parts'])} (bars={bars})")
                ok = True
                break
        if not ok:
            print(f"{sid} v{v['n']}: re-render doesn't match the saved mix, skipped")
