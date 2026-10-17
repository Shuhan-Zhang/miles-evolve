#!/usr/bin/env python3
"""Freeze the 16 train / 16 held-out split the coach is gated on. Briefs come from bench/tasks.json and
evolve/briefs_new.json; only ones stating both BPM and key are eligible. Split is by channel (no uploader on both
sides), stratified by style so both halves get the same mix, deterministic (seeded). Refuses to overwrite.

    python3 evolve/make_split.py   -> evolve/split.json
"""
import hashlib, json, random, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "evolve/split.json"
SEED = 20261017
STYLES = [("rnb", ("rnb", "r&b", "frank ocean", "sza", "brent", "daniel caesar", "summer walker", "6lack",
                   "bryson", "giveon")),
          ("lofi_jazz", ("lofi", "lo-fi", "jazz", "nujabes", "dilla", "soul", "kaytranada")),
          ("boom_bap", ("boom bap", "griselda", "doom", "joey", "j cole", "kendrick")),
          ("trap", ("trap", "future", "lil baby", "gunna", "21 savage", "travis", "toliver", "drake", "metro",
                    "carti", "ken carson", "westwood", "drill", "jersey", "afro"))]


def style(t):
    q = (t["query"] + " " + t["title"]).lower()
    return next((s for s, keys in STYLES if any(k in q for k in keys)), "other")


def main():
    if DEST.exists() and "--force" not in sys.argv:
        sys.exit(f"{DEST} is frozen; pass --force only if no coach run has used it")
    pool = json.loads((ROOT / "bench/tasks.json").read_text())
    pool += json.loads((ROOT / "evolve/briefs_new.json").read_text())
    briefs = [{k: t.get(k) for k in ("id", "query", "title", "channel", "stated_bpm", "stated_key", "audio")}
              | {"stated_key": "".join(str(t.get("stated_key") or "").split()) or None}
              | {"style": style(t), "source": "bench/tasks.json" if i < 16 else "evolve/briefs_new.json"}
              for i, t in enumerate(pool) if t.get("stated_bpm") and t.get("stated_key")]
    by_ch = defaultdict(list)
    for b in briefs:
        by_ch[b["channel"]].append(b)
    if len(briefs) < 32:
        sys.exit(f"only {len(briefs)} briefs state BPM and key; need 32")
    # channel groups, stratified by style: shuffle within each style, alternate train / held-out
    rng = random.Random(SEED)
    groups = defaultdict(list)
    for ch, bs in sorted(by_ch.items()):
        groups[bs[0]["style"]].append(bs)
    train, held = [], []
    turn = 0
    for s in sorted(groups):
        gs = groups[s]
        rng.shuffle(gs)
        for g in gs:
            side = train if (turn % 2 == 0 and len(train) < 16) or len(held) >= 16 else held
            if len(side) + len(g) > 16:
                side = held if side is train else train
            if len(side) + len(g) <= 16:
                side.extend(g)
            turn += 1
    assert len(train) == 16 and len(held) == 16, (len(train), len(held))
    assert not {b["channel"] for b in train} & {b["channel"] for b in held}
    body = {"seed": SEED, "rule": "by channel, stratified by style; only briefs stating BPM and key",
            "train": train, "held_out": held}
    body["sha256"] = hashlib.sha256(json.dumps([train, held], sort_keys=True).encode()).hexdigest()
    DEST.write_text(json.dumps(body, indent=1, ensure_ascii=False))
    for name, side in (("train", train), ("held_out", held)):
        mix = defaultdict(int)
        for b in side:
            mix[b["style"]] += 1
        print(name, dict(mix))
        for b in side:
            print(f"  {b['style']:9} {b['stated_bpm']:5} {b['stated_key']:8} {b['channel'][:18]:18} {b['title'][:60]}")
    print("sha256", body["sha256"])


if __name__ == "__main__":
    main()
