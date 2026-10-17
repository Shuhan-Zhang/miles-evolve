#!/usr/bin/env python3
"""The improvement loop: score → coach → patch → re-score → keep only if it beats the best by more than the noise.

    ~/logicagent/.venv/bin/python evolve/run.py --gens 6                 # new loop on the train split, 4 workers
    ~/logicagent/.venv/bin/python evolve/run.py --gens 6 --resume        # pick up the latest loop where it stopped
    ~/logicagent/.venv/bin/python evolve/run.py heldout                  # v0 vs the loop's best on held-out, 2 seeds

Options: --base evolve/versions/v0 · --workers 4 · --seeds 1 · --noise N (pass-count gap a patch must beat; default
from --baseline-twice, else 1) · --baseline-twice (score v0 twice first, noise = the gap) · --baseline <run dir> (reuse
a finished v0 train run) · --briefs 0-3 (a slice of train, for the live demo round).

Train/held-out come from evolve/split.json; the coach only ever sees train results. State lives in
evolve/runs/loop-<stamp>/loop.json (read by the watch view), every generation is a row in evolve/ledger.md, and each
candidate is a version folder evolve/versions/loop-<stamp>/gN. EVOLVE_FAKE_POOL=1 fakes the pool (no Mac needed),
EVOLVE_FAKE_LLM=1 fakes the coach (no key needed).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evolve import coach, llm  # noqa: E402

EVOLVE = ROOT / "evolve"
RUNS = EVOLVE / "runs"
VERSIONS = EVOLVE / "versions"
LEDGER = EVOLVE / "ledger.md"
SPLIT = EVOLVE / "split.json"


# ------------------------------------------------------------------ briefs
def split(name: str) -> list[dict]:
    """Tasks of one side of evolve/split.json. Entries may be task dicts, indices into bench/tasks.json, or ids."""
    if not SPLIT.exists():
        tasks = json.loads((ROOT / "bench/tasks.json").read_text())
        print(f"! {SPLIT} missing: using bench/tasks.json {'first' if name == 'train' else 'second'} half", flush=True)
        h = len(tasks) // 2
        return tasks[:h] if name == "train" else tasks[h:]
    s = json.loads(SPLIT.read_text())
    side = s.get(name) or s.get(name.replace("_", "")) or s.get({"held_out": "heldout"}.get(name, name)) or []
    if side and all(isinstance(x, dict) for x in side):
        return side
    tasks = json.loads((ROOT / (s.get("tasks") or "bench/tasks.json")).read_text())
    by_id = {str(t.get("id") or t.get("query")): t for t in tasks}
    return [tasks[x] if isinstance(x, int) else by_id[str(x)] for x in side]


def pick(tasks: list[dict], spec: str | None) -> list[dict]:
    if not spec:
        return tasks
    if "," in spec:
        return [tasks[int(i)] for i in spec.split(",")]
    a, _, b = spec.partition("-")
    return tasks[int(a): int(b or a) + 1]


# ------------------------------------------------------------------ scoring
def score(version: Path, briefs: list[dict], seeds, workers: int, stamp: str) -> list[dict]:
    if os.environ.get("EVOLVE_FAKE_POOL"):
        return fake_pool(version, briefs, seeds, stamp)
    from evolve import pool
    return pool.run(briefs, version, seeds=seeds, workers=workers, stamp=stamp,
                    on_result=lambda r: print(f"   {r['worker']} {r['brief']}-s{r['seed']} "
                                              f"{'PASS' if r['pass'] else 'fail'}", flush=True))


def load_run(run_dir: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(Path(run_dir).glob("*-s*.json"))]


def strict_pass(r: dict) -> bool:
    """A pass the gate counts: Clean must have been graded (revised beats without stems skip Clean, and the coach
    must not learn that revising a beat dodges the check)."""
    best = (r.get("check") or {}).get("best") or {}
    return bool(r.get("pass")) and best.get("clean") is True


def passes(results: list[dict]) -> int:
    return sum(strict_pass(r) for r in results)


def checks(results: list[dict]) -> dict:
    out = {}
    for k in ("delivered", "tempo", "key", "clean"):
        out[k] = sum(bool(((r.get("check") or {}).get("best") or {}).get(k)) for r in results)
    return out


def fake_pool(version: Path, briefs, seeds, stamp) -> list[dict]:
    """Offline stand-in for pool.run: pass odds rise a little with each added rule or flag."""
    version = Path(version)
    n_rules = sum(1 for _ in (version / "addendum.md").read_text().splitlines()) if (version / "addendum.md").exists() else 0
    flags = json.loads((version / "flags.json").read_text()) if (version / "flags.json").exists() else {}
    p = min(0.9, 0.45 + 0.08 * n_rules + 0.12 * (flags.get("MILES_SELF_CHECK") == "1"))
    out_dir = RUNS / stamp / version.name
    out_dir.mkdir(parents=True, exist_ok=True)
    res = []
    for t in briefs:
        for s in seeds:
            ok = {k: random.random() < (p ** 0.25) for k in ("tempo", "key", "clean")}
            best = {"delivered": True, **ok, "pass": all(ok.values()),
                    "why": [f"{k}: fake failure" for k, v in ok.items() if not v], "measured": {"bpm": 90, "key": "Am"}}
            bid = str(t.get("id") or t.get("query"))
            r = {"brief": bid, "seed": s, "version": version.name, "text": t.get("title", bid), "seconds": 40,
                 "check": {"best": best, "pass": best["pass"]}, "pass": best["pass"]}
            (out_dir / f"{bid}-s{s}.json").write_text(json.dumps(r))
            res.append(r)
    time.sleep(0.2)
    return res


# ------------------------------------------------------------------ state
def save(state: dict):
    p = RUNS / state["loop"] / "loop.json"
    state["spent"] = {**state.get("spent", {}), "llm_usd": round(state.get("llm_usd0", 0) + llm.SPENT["usd"], 4),
                      "llm_calls": state.get("llm_calls0", 0) + llm.SPENT["calls"]}
    state["at"] = time.time()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(p)


def latest_loop() -> str | None:
    loops = sorted(p.parent.name for p in RUNS.glob("loop-*/loop.json"))
    return loops[-1] if loops else None


def ledger_row(state: dict, h: dict):
    if not LEDGER.exists() or not LEDGER.read_text().strip():
        LEDGER.write_text("# Evolve ledger\n\nOne row per generation. Train split only; the coach never sees held-out. "
                          "A patch is kept only if train passes beat the best by more than the noise floor.\n\n"
                          "| loop | gen | hypothesis (coach) | patch | train before → after | noise | kept | ear |\n"
                          "|---|---|---|---|---|---|---|---|\n")
    hyp = h["hypothesis"].replace("|", "/").replace("\n", " ")
    with LEDGER.open("a") as f:
        f.write(f"| {state['loop']} | {h['gen']} | {hyp} | {h['patch_desc']} | {h['before']}/{h['n']} → "
                f"{h['after']}/{h['n']} | {state['noise']} | {'✓ KEPT' if h['kept'] else '✗'} | "
                f"{'machine-kept, awaiting your ear' if h['kept'] else ''} |\n")


def queue_ear(version: Path, base: Path):
    """Kept patches go to the human's blind A/B, never straight to prod. EVOLVE_EAR_CMD is a shell template with
    {base} and {version} (e.g. the eval/make_batch.py call); unset, the request is only recorded."""
    rec = {"version": str(version), "base": str(base), "label": "machine-kept, awaiting your ear", "at": time.time()}
    cmd = os.environ.get("EVOLVE_EAR_CMD")
    if cmd:
        try:
            r = subprocess.run(cmd.format(base=base, version=version), shell=True, cwd=ROOT, capture_output=True,
                               text=True, timeout=600)
            rec["cmd"], rec["rc"], rec["tail"] = cmd, r.returncode, (r.stdout + r.stderr)[-500:]
        except Exception as e:                                                   # noqa: BLE001
            rec["error"] = repr(e)[:200]
    q = EVOLVE / "ear_queue.json"
    items = json.loads(q.read_text()) if q.exists() else []
    q.write_text(json.dumps(items + [rec], indent=1))


# ------------------------------------------------------------------ the loop
def ensure_base(base: Path):
    if (base / "MILES.md").exists():
        return
    base.mkdir(parents=True, exist_ok=True)
    src = ROOT / "data/MILES.md"
    (base / "MILES.md").write_text(src.read_text() if src.exists() else "")
    (base / "addendum.md").write_text("")
    (base / "flags.json").write_text(json.dumps({"MILES_SELF_CHECK": "0"}, indent=1) + "\n")
    print(f"made {base} from {src}", flush=True)


def loop(a):
    RUNS.mkdir(parents=True, exist_ok=True)
    train = pick(split("train"), a.briefs)
    seeds = list(range(1, a.seeds + 1))
    if a.resume and latest_loop():
        state = json.loads((RUNS / latest_loop() / "loop.json").read_text())
        state["gens"] = max(state["gens"], a.gens)
        state["llm_usd0"], state["llm_calls0"] = state["spent"].get("llm_usd", 0), state["spent"].get("llm_calls", 0)
        print(f"resuming {state['loop']} at gen {state['gen'] + 1}/{state['gens']}", flush=True)
    else:
        base = Path(a.base)
        ensure_base(base)
        name = "loop-" + time.strftime("%Y%m%d-%H%M%S")
        (RUNS / name).mkdir(parents=True)
        state = {"loop": name, "gen": 0, "gens": a.gens, "base": str(base), "best": str(base), "best_pass": None,
                 "noise": a.noise, "n": len(train) * len(seeds), "workers": a.workers, "seeds": a.seeds,
                 "briefs": [str(t.get("id") or t.get("query")) for t in train], "history": [], "current": None,
                 "baseline_runs": []}
        save(state)
    vdir = VERSIONS / state["loop"]
    vdir.mkdir(parents=True, exist_ok=True)

    # gen 0: the baseline (and, if asked, the noise floor: the same version twice)
    if state["best_pass"] is None:
        if a.baseline:
            res = load_run(Path(a.baseline))
            state["baseline_runs"].append(str(a.baseline))
        else:
            stamp = f"{state['loop']}-g0"
            state["current"] = {"gen": 0, "phase": "baseline", "stamp": stamp, "version": Path(state["base"]).name}
            save(state)
            print(f"gen 0: scoring {state['base']} on {len(train)} train briefs × {len(seeds)} seeds", flush=True)
            res = score(Path(state["base"]), train, seeds, a.workers, stamp)
            state["baseline_runs"].append(str(RUNS / stamp / Path(state["base"]).name))
        state["best_pass"], state["best_checks"] = passes(res), checks(res)
        state["best_run"] = state["baseline_runs"][-1]
        if a.baseline_twice and state["noise"] is None:
            stamp = f"{state['loop']}-g0b"
            state["current"] = {"gen": 0, "phase": "noise", "stamp": stamp, "version": Path(state["base"]).name}
            save(state)
            res2 = score(Path(state["base"]), train, seeds, a.workers, stamp)
            state["noise"] = abs(passes(res2) - state["best_pass"])
            state["baseline_runs"].append(str(RUNS / stamp / Path(state["base"]).name))
            print(f"noise floor: {state['best_pass']} vs {passes(res2)} → gap {state['noise']}", flush=True)
        state["v0_pass"] = state["best_pass"]
        if state["noise"] is None:
            state["noise"] = 1
        state["current"] = None
        save(state)
        print(f"baseline: {state['best_pass']}/{state['n']} {state['best_checks']}  noise {state['noise']}", flush=True)

    while state["gen"] < state["gens"]:
        g = state["gen"] + 1
        best = Path(state["best"])
        best_res = load_run(Path(state["best_run"]))
        state["current"] = {"gen": g, "phase": "coach", "version": f"g{g}"}
        save(state)
        print(f"\ngen {g}/{state['gens']}: coach reading {len(best_res) - passes(best_res)} failures of {best.name}",
              flush=True)
        try:
            p = coach.propose(best, best_res, state["history"])
        except Exception as e:                                                   # noqa: BLE001
            print(f"  coach failed: {e}", flush=True)
            state["history"].append({"gen": g, "hypothesis": f"(coach failed: {str(e)[:120]})", "kind": "none",
                                     "patch_desc": "none", "before": state["best_pass"], "after": state["best_pass"],
                                     "n": state["n"], "kept": False, "error": True})
            state["gen"] = g
            save(state)
            continue
        new = coach.apply(p, best, vdir / f"g{g}")
        stamp = f"{state['loop']}-g{g}"
        print(f"  hypothesis: {p['hypothesis']}\n  patch: {coach.describe(p)}", flush=True)
        state["current"] = {"gen": g, "phase": "scoring", "stamp": stamp, "version": new.name,
                            "hypothesis": p["hypothesis"], "patch": p, "patch_desc": coach.describe(p)}
        save(state)
        res = score(new, train, seeds, a.workers, stamp)
        after = passes(res)
        kept = after > state["best_pass"] + state["noise"]
        h = {"gen": g, "hypothesis": p["hypothesis"], "kind": p["kind"], "patch": p, "patch_desc": coach.describe(p),
             "version": str(new), "run": str(RUNS / stamp / new.name), "before": state["best_pass"], "after": after,
             "n": state["n"], "checks": checks(res), "kept": kept}
        state["history"].append(h)
        ledger_row(state, h)
        print(f"  train {h['before']}/{h['n']} → {after}/{h['n']} (noise {state['noise']}) "
              f"{'✓ KEPT' if kept else '✗ rejected'}", flush=True)
        if kept:
            queue_ear(new, best)
            state.update(best=str(new), best_pass=after, best_checks=h["checks"], best_run=h["run"])
        state["gen"] = g
        state["current"] = None
        save(state)
    print(f"\ndone: best {Path(state['best']).name} {state['best_pass']}/{state['n']} "
          f"(v0 {state['v0_pass']}/{state['n']}), coach spend ${state['spent']['llm_usd']}", flush=True)


def heldout(a):
    name = a.loop or latest_loop()
    state = json.loads((RUNS / name / "loop.json").read_text())
    briefs = split("held_out")
    seeds = list(range(1, a.seeds + 1))
    out = state.setdefault("heldout", {})
    for label, v in (("v0", state["base"]), ("best", state["best"])):
        stamp = f"{name}-heldout-{label}"
        state["current"] = {"gen": "held-out", "phase": label, "stamp": stamp, "version": Path(v).name}
        save(state)
        res = score(Path(v), briefs, seeds, a.workers, stamp)
        out[label] = {"version": v, "pass": passes(res), "n": len(res), "checks": checks(res),
                      "run": str(RUNS / stamp / Path(v).name)}
        print(f"held-out {label} ({Path(v).name}): {passes(res)}/{len(res)} {checks(res)}", flush=True)
    state["current"] = None
    save(state)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="loop", choices=["loop", "heldout"])
    ap.add_argument("--gens", type=int, default=6)
    ap.add_argument("--base", default=str(VERSIONS / "v0"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seeds", type=int, default=None)
    ap.add_argument("--noise", type=int, default=None)
    ap.add_argument("--baseline-twice", action="store_true")
    ap.add_argument("--baseline")
    ap.add_argument("--briefs")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--loop", help="heldout: which loop (default latest)")
    a = ap.parse_args()
    if a.cmd == "heldout":
        a.seeds = a.seeds or 2
        heldout(a)
    else:
        a.seeds = a.seeds or 1
        loop(a)


if __name__ == "__main__":
    main()
