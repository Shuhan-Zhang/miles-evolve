#!/usr/bin/env python3
"""More briefs for the self-test: real type-beat titles whose uploader stated BPM *and* key, one per channel,
from channels not already in bench/tasks.json. Reuses bench/collect.py's search, metadata and parsing.
bench/tasks.json is left untouched; output goes to evolve/briefs_new.json.

    ~/logicagent/.venv/bin/python evolve/collect_briefs.py [--want 23]
"""
import json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import collect  # noqa: E402

QUERIES = [
    # R&B
    "rnb type beat", "brent faiyaz type beat", "daniel caesar type beat", "summer walker type beat",
    "6lack type beat", "bryson tiller type beat", "giveon type beat", "frank ocean type beat",
    # lo-fi / jazz hop
    "lofi type beat chill", "jazz hop type beat", "nujabes type beat jazz", "lofi jazz type beat piano",
    "chill lofi hip hop type beat guitar",
    # trap
    "trap type beat dark", "future type beat", "lil baby type beat", "gunna type beat", "21 savage type beat",
    "travis scott type beat dark", "don toliver type beat",
    # boom bap
    "boom bap type beat", "griselda type beat", "mf doom type beat", "joey badass type beat",
    "old school boom bap type beat jazzy", "j cole type beat", "kendrick lamar type beat",
    # second pass
    "kehlani type beat", "smino type beat", "sade type beat rnb", "lofi type beat sad piano",
    "jazzy lofi hip hop type beat", "jazz hop type beat saxophone", "alchemist type beat", "westside gunn type beat",
    "jazzy boom bap type beat", "pop smoke type beat", "dark trap type beat 808",
]


def main():
    want = int(sys.argv[sys.argv.index("--want") + 1]) if "--want" in sys.argv else 23
    old = json.loads((ROOT / "bench/tasks.json").read_text())
    seen = {t["channel"] for t in old}
    refs = ROOT / "bench/refs"
    out, dest = [], ROOT / "evolve/briefs_new.json"
    if dest.exists():  # resume: keep what we have, don't re-fetch those queries
        out = json.loads(dest.read_text())
        seen |= {c["channel"] for c in out}
    done = {c["query"] for c in out}
    for q in QUERIES:
        if len(out) >= want:
            break
        if q in done:
            continue
        res = subprocess.run([collect.YTDLP, "--flat-playlist", "--print", "%(id)s\t%(title)s\t%(duration)s",
                              f"ytsearch10:{q}"], capture_output=True, text=True, timeout=90).stdout.splitlines()
        for line in res:
            vid, title, dur = (line.split("\t") + ["", "", ""])[:3]
            try:
                dur = float(dur)
            except ValueError:
                continue
            if "type beat" not in title.lower() or not 90 <= dur <= 420:
                continue
            m = collect.meta(f"https://www.youtube.com/watch?v={vid}")
            st = collect.stated(m.get("description", ""))
            if "bpm" not in st or "key" not in st or m.get("channel") in seen:
                continue
            seen.add(m.get("channel"))
            cand = {"query": q, "id": vid, "title": title, "channel": m.get("channel"), "seconds": dur,
                    "views": m.get("view_count"), "stated_bpm": st["bpm"], "stated_key": "".join(st["key"].split())}
            path = next(iter(refs.glob(f"{vid}.*")), None)
            if not path:
                subprocess.run([collect.YTDLP, "-f", "bestaudio[ext=m4a]/bestaudio", "-o", str(refs / "%(id)s.%(ext)s"),
                                "--no-playlist", "--no-warnings", f"https://www.youtube.com/watch?v={vid}"],
                               capture_output=True, timeout=240)
                path = next(iter(refs.glob(f"{vid}.*")), None)
            cand["audio"] = str(path) if path else None
            out.append(cand)
            print(f"{len(out):2} {title[:60]:60} {cand['channel'][:18]:18} bpm={st['bpm']} key={cand['stated_key']}", flush=True)
            dest.write_text(json.dumps(out, indent=1, ensure_ascii=False))
            break
        else:
            print("no stated bpm+key for", q, flush=True)
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"{len(out)} briefs -> {dest}")


if __name__ == "__main__":
    main()
