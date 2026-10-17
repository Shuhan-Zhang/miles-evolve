#!/usr/bin/env python3
"""Parallel worker pool: N scratch studios (one data dir and port each), so a batch of briefs runs N at a time.

    ~/logicagent/.venv/bin/python evolve/pool.py run --version evolve/versions/v0 --briefs 0-7 [--seeds 1] [--workers 4]
    ~/logicagent/.venv/bin/python evolve/pool.py verify-render        # the renderer: 4 data dirs at once == 1 alone
    ~/logicagent/.venv/bin/python evolve/pool.py agree evolve/runs/<a>/v0 evolve/runs/<b>/v0   # the noise floor

Worker N is `server/app.py --port 886N` (POOL_BASE_PORT) with MILES_STUDIO_DATA=evolve/work/wN (library, chops and stems are symlinked
read-only by engine.py, like data/engine). Never `bin/studio`: it pkills every server/app.py, prod included. Workers are
started and stopped only by this module, through their own process handles.

A version is a folder: MILES.md (the playbook), addendum.md (extra rules, appended to the playbook, which the producer
reads into its system prompt), flags.json ({"ENV": "value"} for the worker processes, e.g. MILES_SELF_CHECK, MILES_BEST_OF).
Every job runs in a fresh session with the version's playbook rewritten first (the producer's `remember` tool can edit
MILES.md mid-run; one job must not leak into the next).

Results: evolve/runs/<stamp>/<version>/<brief>-s<seed>.json, plus summary.json; live worker state in
evolve/runs/<stamp>/status.json (for the watch view). A seed is a replicate label: the engine picks its own luck per
song (engine.seed from os.urandom) and the producer model isn't seeded, so two seeds are two independent tries.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVOLVE = ROOT / "evolve"
WORK = EVOLVE / "work"
RUNS = EVOLVE / "runs"
PY = Path(os.environ.get("LOGICAGENT") or Path.home() / "logicagent") / ".venv/bin/python"
BASE_PORT = int(os.environ.get("POOL_BASE_PORT", 8860))  # worker N listens on BASE + N (8861..); 8840s are other threads'
JOB_TIMEOUT = 300                                  # seconds; the Delivered check has its own 180 s bar


# ------------------------------------------------------------------ briefs
def clean(title: str) -> str:
    """bench/run.py's: drop [FREE] tags and anything after a |."""
    t = re.sub(r"[\[(](?:free|sold|free for profit)[\])]\s*", "", title, flags=re.I)
    t = re.sub(r"\s*\|.*$", "", t)
    return re.sub(r"\s+", " ", t).strip(" -")


def key_words(k: str | None) -> str | None:
    """'Bmin' -> 'B minor', 'F#Major' -> 'F# major'."""
    m = re.match(r"\s*([A-G][#b]?)\s*(maj(?:or)?|min(?:or)?|m)?\s*$", k or "", flags=re.I)
    if not m:
        return k or None
    mode = (m.group(2) or "maj").lower()
    return f"{m.group(1)} {'major' if mode.startswith('maj') else 'minor'}"


def brief_text(task: dict, per: int = 1) -> str:
    """What the worker is told, the way a producer would type it; the task's own `brief` wins if it has one."""
    if task.get("brief"):
        return task["brief"]
    bits = [clean(task["title"])]
    if task.get("stated_bpm"):
        bits.append(f"{task['stated_bpm']:g} BPM")
    if key_words(task.get("stated_key")):
        bits.append(key_words(task["stated_key"]))
    return ", ".join(bits) + f" — make {per} beat{'s' if per != 1 else ''} like that"


def brief_id(task: dict) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(task.get("id") or task["query"]))


def load_briefs(spec: str | None, path: Path | None = None) -> list[dict]:
    """bench/tasks.json (or `path`), all or a slice: '0-7', '1,4,5'."""
    tasks = json.loads((path or ROOT / "bench/tasks.json").read_text())
    if isinstance(tasks, dict):                    # a split file: {"train": [...], "held_out": [...]}
        raise SystemExit("pass a list of tasks (pick train or held_out from the split first)")
    if not spec:
        return tasks
    if "," in spec:
        return [tasks[int(i)] for i in spec.split(",")]
    a, _, b = spec.partition("-")
    return tasks[int(a): int(b or a) + 1]


# ------------------------------------------------------------------ versions
def version_files(version: Path) -> tuple[str, dict]:
    """(playbook text, env flags) for a version folder."""
    version = Path(version)
    book = (version / "MILES.md").read_text() if (version / "MILES.md").exists() else ""
    if (version / "addendum.md").exists():
        add = (version / "addendum.md").read_text().strip()
        if add:
            book = book.rstrip() + "\n\n## Added rules\n" + add + "\n"
    flags = json.loads((version / "flags.json").read_text()) if (version / "flags.json").exists() else {}
    return book, {str(k): str(v) for k, v in flags.items()}


# ------------------------------------------------------------------ http
def call(port: int, method: str, path: str, body: dict | None = None, timeout: float = 30):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read() or b"null")


def up(port: int) -> bool:
    try:
        return call(port, "GET", "/api/state", timeout=3)[0] == 200
    except Exception:
        return False


# ------------------------------------------------------------------ workers
class Worker:
    def __init__(self, n: int):
        self.n, self.port = n, BASE_PORT + n
        self.data = WORK / f"w{n}"
        self.proc: subprocess.Popen | None = None
        self.book = ""

    def start(self, book: str, flags: dict):
        if up(self.port):
            raise RuntimeError(f"port {self.port} is already serving: stop that server first (pool.py stop)")
        self.data.mkdir(parents=True, exist_ok=True)
        self.book = book
        (self.data / "MILES.md").write_text(book)
        env = dict(os.environ, MILES_STUDIO_DATA=str(self.data), **flags)
        log = open(self.data / "server.out", "ab")
        self.proc = subprocess.Popen([str(PY), str(ROOT / "server/app.py"), "--port", str(self.port)], cwd=ROOT,
                                     env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        (self.data / "pid").write_text(str(self.proc.pid))

    def wait_up(self, timeout: float = 180):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError(f"w{self.n} exited ({self.proc.returncode}); see {self.data / 'server.out'}")
            if up(self.port):
                return
            time.sleep(0.5)
        raise RuntimeError(f"w{self.n} not up after {timeout:.0f} s; see {self.data / 'server.out'}")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
        (self.data / "pid").unlink(missing_ok=True)

    def job(self, text: str, name: str, timeout: float = JOB_TIMEOUT) -> dict:
        (self.data / "MILES.md").write_text(self.book)          # the version's playbook, untouched by the last job
        call(self.port, "POST", "/api/sessions", {"name": name})
        t0 = time.time()
        try:
            status, out = call(self.port, "POST", "/api/task", {"text": text, "timeout": timeout}, timeout=timeout + 30)
        except urllib.error.HTTPError as e:
            return {"error": f"HTTP {e.code}: {e.read()[:200].decode(errors='replace')}", "songs": [],
                    "seconds": round(time.time() - t0, 1)}
        except Exception as e:
            return {"error": repr(e)[:200], "songs": [], "seconds": round(time.time() - t0, 1)}
        out = out or {}
        if status == 202:                                         # still working at the timeout: stop it
            out["error"] = "timeout"
        out["seconds"] = round(time.time() - t0, 1)
        return out


def stop_strays():
    """Stop workers a crashed run left behind (by the pid each one wrote), never anything else."""
    for pid_file in WORK.glob("w*/pid"):
        try:
            pid = int(pid_file.read_text())
            cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
            if "server/app.py" in cmd and f"--port {BASE_PORT // 10}" in cmd:
                os.killpg(pid, signal.SIGTERM)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
        pid_file.unlink(missing_ok=True)


# ------------------------------------------------------------------ self-test (W2's checker, if it's there)
def _checker():
    try:
        sys.path.insert(0, str(ROOT))
        from evolve import check, contract                       # noqa: F401
        return check, contract
    except Exception as e:                                        # not built yet: results still record the songs
        return None, repr(e)[:200]


_SPLIT: dict | None = None


def _split() -> dict | None:
    """evolve/split.json, for contract()'s same-side reference audio (None if it's missing or unreadable)."""
    global _SPLIT
    if _SPLIT is None:
        try:
            _SPLIT = json.loads((ROOT / "evolve/split.json").read_text())
        except (OSError, ValueError):
            return None
    return _SPLIT


def self_test(task: dict, out: dict) -> dict | None:
    """evolve/check.py on every song the job made; the brief passes if its best song does."""
    check, contract = _checker()
    if check is None:
        return {"error": f"evolve.check unavailable: {contract}"}
    try:
        c = contract.contract(task, _split())
        songs = [s for s in out.get("songs") or [] if s.get("audio")]
        per = [check.check(Path(s["audio"]).parent, c, out.get("seconds")) for s in songs]
        if not per:
            per = [{"song": None, "tempo": None, "key": None, "clean": None, "delivered": False, "pass": False,
                    "why": [f"not delivered: no song came back ({out.get('error') or 'no error'})"], "contract": c}]
        best = max(per, key=lambda r: (bool(r.get("pass")), sum(bool(r.get(k)) for k in
                                                                   ("delivered", "tempo", "key", "clean"))))
        return {"contract": c, "songs": per, "best": best, "pass": bool(best.get("pass"))}
    except Exception as e:
        return {"error": repr(e)[:300]}


# ------------------------------------------------------------------ the run
def run(briefs: list[dict], version, seeds=(1,), workers: int = 4, stamp: str | None = None, per: int = 1,
        timeout: float = JOB_TIMEOUT, on_result=None) -> list[dict]:
    """Every (brief, seed) through the producer on `workers` scratch studios at once; each result self-tested and
    written to evolve/runs/<stamp>/<version>/<brief>-s<seed>.json. Returns the results in brief, seed order."""
    version = Path(version)
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    out_dir = RUNS / stamp / version.name
    out_dir.mkdir(parents=True, exist_ok=True)
    book, flags = version_files(version)
    (out_dir / "version.json").write_text(json.dumps({"version": str(version), "playbook": book, "flags": flags,
                                                      "workers": workers, "per": per}, indent=1))
    jobs: queue.Queue = queue.Queue()
    order = []
    for t in briefs:
        for s in seeds:
            order.append((brief_id(t), s))
            jobs.put((t, s))
    status = {"stamp": stamp, "version": version.name, "total": len(order), "done": 0, "workers": {}}
    lock = threading.Lock()
    results: dict[tuple, dict] = {}

    def set_status(n, **kw):
        with lock:
            status["workers"][f"w{n}"] = {**status["workers"].get(f"w{n}", {}), **kw, "at": time.time()}
            (RUNS / stamp / "status.json").write_text(json.dumps(status, indent=1))

    pool = [Worker(n) for n in range(1, workers + 1)]
    stop_strays()
    try:
        for w in pool:
            w.start(book, flags)
        for w in pool:
            set_status(w.n, state="starting")
            w.wait_up()
            set_status(w.n, state="idle")

        def loop(w: Worker):
            while True:
                try:
                    task, seed = jobs.get_nowait()
                except queue.Empty:
                    set_status(w.n, state="idle", brief=None)
                    return
                bid = brief_id(task)
                text = brief_text(task, per)
                set_status(w.n, state="building", brief=bid, seed=seed, title=clean(task.get("title", bid)))
                out = w.job(text, f"evolve · {version.name} · {bid} · s{seed}", timeout)
                set_status(w.n, state="verifying")
                test = self_test(task, out)
                res = {"brief": bid, "seed": seed, "version": version.name, "worker": f"w{w.n}", "text": text,
                       "task": task, **out, "check": test, "pass": bool(test and test.get("pass"))}
                (out_dir / f"{bid}-s{seed}.json").write_text(json.dumps(res, indent=1))
                with lock:
                    results[(bid, seed)] = res
                    status["done"] += 1
                set_status(w.n, state="done", brief=bid, seed=seed, passed=res["pass"],
                           best=(test or {}).get("best"))
                if on_result:
                    on_result(res)

        threads = [threading.Thread(target=loop, args=(w,), daemon=True) for w in pool]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        for w in pool:
            w.stop()
    ordered = [results[k] for k in order if k in results]
    (out_dir / "summary.json").write_text(json.dumps(summary(ordered), indent=1))
    return ordered


def summary(results: list[dict]) -> dict:
    """Pass count, and per check how many runs passed it out of how many it graded (a contract with no stated key
    doesn't grade key: None is not a miss)."""
    def n(k):
        vals = [((r.get("check") or {}).get("best") or {}).get(k) for r in results]
        return {"ok": sum(v is True for v in vals), "graded": sum(v is not None for v in vals)}
    secs = [r["seconds"] for r in results if r.get("seconds")]
    return {"runs": len(results), "pass": sum(r["pass"] for r in results), "delivered": n("delivered"),
            "tempo": n("tempo"), "key": n("key"), "clean": n("clean"),
            "errors": sum(bool(r.get("error")) for r in results),
            "seconds_mean": round(sum(secs) / len(secs), 1) if secs else None}


def load(run_dir: Path) -> dict[tuple, dict]:
    return {(r["brief"], r["seed"]): r for r in (json.loads(p.read_text()) for p in Path(run_dir).glob("*-s*.json"))}


def agree(a_dir: Path, b_dir: Path) -> dict:
    """Two runs of the same version on the same briefs and seeds: how far apart they land is the noise floor."""
    a, b = load(a_dir), load(b_dir)
    keys = sorted(set(a) & set(b))
    flips = [k for k in keys if a[k]["pass"] != b[k]["pass"]]
    per_check = {}
    for c in ("delivered", "tempo", "key", "clean"):
        get = lambda r: ((r.get("check") or {}).get("best") or {}).get(c)  # noqa: E731
        per_check[c] = {"a": sum(get(a[k]) is True for k in keys), "b": sum(get(b[k]) is True for k in keys),
                        "graded": sum(get(a[k]) is not None for k in keys),
                        "flips": sum(get(a[k]) != get(b[k]) for k in keys)}
    pa, pb = sum(a[k]["pass"] for k in keys), sum(b[k]["pass"] for k in keys)
    return {"pairs": len(keys), "pass_a": pa, "pass_b": pb, "gap": abs(pa - pb), "flips": len(flips),
            "flipped": [f"{k[0]}-s{k[1]}" for k in flips], "per_check": per_check}


# ------------------------------------------------------------------ renderer check (no producer, no LLM)
def verify_render(workers: int = 4, src: str | None = None) -> dict:
    """The same render state through the renderer in `workers` separate ENGINEER_KIT_ROOTs at once, against the same
    state rendered alone: the beats must match sample for sample (or within float noise), and N at once should take
    well under N times as long."""
    import numpy as np
    import soundfile as sf
    sys.path.insert(0, str(ROOT / "server"))
    os.environ.setdefault("MILES_STUDIO_DATA", str(WORK / "w1"))
    import engine
    states = sorted([*(ROOT / "data/engine").glob("render_s_*.json"),            # a fork may have no songs yet:
                     *(Path.home() / "miles-studio/data/engine").glob("render_s_*.json")],  # read prod's states only
                    key=lambda p: p.stat().st_mtime)
    state = Path(src) if src else states[-1]
    renderer = engine.RENDER if engine.RENDER.exists() else None
    if renderer is None:
        raise SystemExit(f"no headless renderer at {engine.RENDER}")

    def one(root: Path, tag: str):
        eng = root / "engine"
        eng.mkdir(parents=True, exist_ok=True)
        for name in ("library", "chops", "stems"):
            link, real = eng / name, engine.LIVE_LA / name
            if real.exists() and not link.exists():
                link.symlink_to(real, target_is_directory=True)
        st = eng / f"render_verify_{tag}.json"
        shutil.copyfile(state, st)
        env = dict(os.environ, EKS_ACTIONS="bounce", ENGINEER_KIT_FAKE_PLAYHEAD="120.000", ENGINEER_KIT_ROOT=str(eng))
        bpm = json.loads(state.read_text()).get("bpm")
        if bpm:
            env["ENGINEER_KIT_FAKE_PLAYHEAD"] = f"{float(bpm):.3f}"
        return subprocess.Popen([str(renderer), str(st)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def beat_of(proc) -> tuple[str | None, str]:
        so, _ = proc.communicate(timeout=180)
        text = so.decode(errors="replace")
        for line in text.splitlines():
            if line.startswith("bounce beat "):
                return line[len("bounce beat "):].strip(), text
        return None, text

    def read(path):
        y, sr = sf.read(path, dtype="float32")
        return y, sr

    t0 = time.time()
    alone_path, log = beat_of(one(WORK / "solo", "solo"))
    t_alone = time.time() - t0
    if not alone_path:
        raise SystemExit("solo render failed:\n" + log[-800:])
    alone_copy = WORK / "solo" / "beat.wav"
    shutil.copyfile(alone_path, alone_copy)
    ref, sr = read(alone_copy)
    t0 = time.time()
    procs = [one(WORK / f"w{n}", f"w{n}") for n in range(1, workers + 1)]
    outs = [beat_of(p) for p in procs]
    t_par = time.time() - t0
    rows = []
    for n, (path, log) in enumerate(outs, 1):
        if not path:
            rows.append({"worker": f"w{n}", "ok": False, "log": log[-300:]})
            continue
        y, sr2 = read(path)
        same_len = y.shape == ref.shape
        diff = float(np.max(np.abs(y - ref))) if same_len else None
        rows.append({"worker": f"w{n}", "ok": same_len and sr2 == sr and diff is not None and diff < 1e-4,
                     "path": path, "seconds": round(len(y) / sr2, 2), "max_diff": diff})
    return {"state": str(state), "solo_seconds": round(t_alone, 1), "parallel_seconds": round(t_par, 1),
            "workers": workers, "speedup": round(workers * t_alone / t_par, 2) if t_par else None, "renders": rows,
            "ok": all(r["ok"] for r in rows)}


# ------------------------------------------------------------------ cli
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--version", required=True)
    r.add_argument("--briefs", help="slice of the task list: 0-7 or 1,4,5")
    r.add_argument("--tasks", help="task list json (default bench/tasks.json)")
    r.add_argument("--seeds", type=int, default=1)
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--per", type=int, default=1, help="beats asked for per brief")
    r.add_argument("--stamp")
    r.add_argument("--timeout", type=float, default=JOB_TIMEOUT)
    v = sub.add_parser("verify-render")
    v.add_argument("--workers", type=int, default=4)
    v.add_argument("--state")
    g = sub.add_parser("agree")
    g.add_argument("a")
    g.add_argument("b")
    sub.add_parser("stop")
    a = ap.parse_args()
    if a.cmd == "run":
        briefs = load_briefs(a.briefs, Path(a.tasks) if a.tasks else None)

        def show(res):
            b = (res.get("check") or {}).get("best") or {}
            marks = " ".join(f"{k} {'·' if b.get(k) is None else '✓' if b.get(k) else '✗'}" for k in ("delivered", "tempo", "key", "clean"))
            print(f"{res['worker']}  {res['brief']}-s{res['seed']}  {res.get('seconds')}s  {marks}  "
                  f"{'PASS' if res['pass'] else 'fail'}  {res.get('error') or (res.get('check') or {}).get('error') or ''}",
                  flush=True)
        res = run(briefs, a.version, range(1, a.seeds + 1), a.workers, a.stamp, a.per, a.timeout, show)
        print(json.dumps(summary(res), indent=1))
    elif a.cmd == "verify-render":
        print(json.dumps(verify_render(a.workers, a.state), indent=1))
    elif a.cmd == "agree":
        print(json.dumps(agree(Path(a.a), Path(a.b)), indent=1))
    elif a.cmd == "stop":
        stop_strays()


if __name__ == "__main__":
    main()
