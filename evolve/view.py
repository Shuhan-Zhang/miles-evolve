#!/usr/bin/env python3
"""evolve watch: a live terminal view of the improvement loop. Read-only: it never writes, starts or stops anything.

    python evolve/view.py              # refresh every 2 s (Ctrl-C to quit)
    python evolve/view.py --once       # print one frame (screenshots, tests)
    python evolve/view.py --svg f.svg  # save one frame as SVG (width 120)
    python evolve/view.py --loop loop-20261017-141624 --width 140

It reads what run.py and pool.py write: evolve/runs/loop-*/loop.json (loop state, history, held-out),
evolve/runs/<stamp>/status.json (live workers), evolve/runs/<stamp>/<version>/<brief>-s<seed>.json (per-brief
self-test), evolve/ledger.md and evolve/runs/loop.log. Missing or half-written files show as "pending", never crash.
Needs `rich` (pip install rich).
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

EVOLVE = Path(__file__).resolve().parent
RUNS = EVOLVE / "runs"
VERSIONS = EVOLVE / "versions"
LEDGER = EVOLVE / "ledger.md"
LOG = RUNS / "loop.log"
OK, BAD, NA = "[green]✓[/]", "[red]✗[/]", "[dim]·[/]"


# ------------------------------------------------------------------ reading (all tolerant of missing/partial files)
def jload(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def latest_loop() -> str | None:
    loops = sorted(p.parent.name for p in RUNS.glob("loop-*/loop.json"))
    return loops[-1] if loops else None


def run_dir_of(state: dict) -> Path | None:
    """The generation on screen: the one being scored now, else the last scored one, else the baseline."""
    cur = state.get("current") or {}
    if cur.get("stamp") and cur.get("version"):
        return RUNS / cur["stamp"] / cur["version"]
    hist = state.get("history") or []
    for h in reversed(hist):
        if h.get("run"):
            return Path(h["run"])
    if state.get("best_run"):
        return Path(state["best_run"])
    runs = state.get("baseline_runs") or []
    return Path(runs[-1]) if runs else None


def results(run_dir: Path | None) -> list[dict]:
    if not run_dir or not run_dir.exists():
        return []
    out = []
    for p in sorted(run_dir.glob("*-s*.json")):
        r = jload(p)
        if isinstance(r, dict):
            out.append(r)
    return out


def strict_pass(r: dict) -> bool:
    """run.py's gate: a pass only counts if Clean was graded."""
    best = (r.get("check") or {}).get("best") or {}
    return bool(r.get("pass")) and best.get("clean") is True


def ledger_rows() -> list[list[str]]:
    rows = []
    try:
        lines = LEDGER.read_text().splitlines()
    except OSError:
        return rows
    for ln in lines:
        if not ln.startswith("|") or ln.startswith("|---") or "hypothesis" in ln:
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) >= 7:
            rows.append(cells)
    return rows


def log_tail(n: int = 6) -> list[str]:
    try:
        lines = LOG.read_text(errors="replace").splitlines()
    except OSError:
        return []
    keep = [ln for ln in lines if ln.strip() and "Warning" not in ln and "return super()" not in ln]
    return keep[-n:]


def model_name(state: dict) -> str:
    flags = jload(Path(state.get("best") or VERSIONS / "v0") / "flags.json", {}) or {}
    m = flags.get("CRUSOE_PRODUCER_MODEL") or "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B"
    return m.split("/")[-1].replace("NVIDIA-", "")


def clean_title(t: str) -> str:
    t = re.sub(r"[\[(]\s*(?:free|sold|free for profit)\s*[\])]\s*", "", t or "", flags=re.I)
    t = re.sub(r"\s*\|.*$", "", t)
    return re.sub(r"\s+", " ", t).strip(" -")


def short(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


# ------------------------------------------------------------------ panels
def header(state: dict | None) -> Panel:
    if not state:
        return Panel(Text("MILES N+1 · Crusoe Nemotron · no loop yet (evolve/runs/loop-*/loop.json)", style="bold"),
                     box=box.HEAVY)
    gen, gens = state.get("gen", 0), state.get("gens", "?")
    cur = state.get("current") or {}
    t = Text()
    t.append("MILES N+1", style="bold magenta")
    t.append(f" · Crusoe {model_name(state)} · ", style="bold")
    t.append(f"gen {cur.get('gen', gen)}/{gens}", style="bold cyan")
    phase = cur.get("phase")
    if phase:
        t.append(f"  ({phase})", style="yellow")
    t.append(f"   loop {state.get('loop')}", style="dim")
    line2 = Text()
    hist = state.get("history") or []
    n = state.get("n", "?")
    if hist:
        h = hist[-1]
        line2.append(f"latest train  gen {h['gen']}: {h['before']}/{h['n']} → {h['after']}/{h['n']}  ")
        if h.get("error"):
            line2.append("coach failed", style="bold red")
        else:
            line2.append("✓ KEPT" if h.get("kept") else "✗ REJECTED", style="bold green" if h.get("kept") else "bold red")
    elif state.get("best_pass") is not None:
        line2.append(f"baseline v0: {state['best_pass']}/{n} train passes", style="bold")
    else:
        line2.append("baseline v0: scoring…", style="yellow")
    if state.get("best_pass") is not None:
        line2.append(f"   best {Path(state.get('best', '')).name} {state['best_pass']}/{n}"
                     f" · noise floor {state.get('noise')}", style="cyan")
    spent = state.get("spent") or {}
    if spent.get("llm_calls"):
        line2.append(f"   coach ${spent.get('llm_usd', 0)} · {spent['llm_calls']} calls", style="dim")
    return Panel(Group(t, line2), box=box.HEAVY)


def workers_panel(state: dict | None) -> Panel:
    run_dir = run_dir_of(state) if state else None
    res = results(run_dir)
    status = jload(run_dir.parent / "status.json", {}) if run_dir else {}
    tb = Table(box=box.SIMPLE_HEAD, expand=True, pad_edge=False)
    tb.add_column("w", style="dim", width=3)
    tb.add_column("brief", ratio=3, no_wrap=True)
    tb.add_column("tempo", min_width=13, no_wrap=True)
    tb.add_column("key", min_width=10, no_wrap=True)
    tb.add_column("clean", ratio=3, no_wrap=True)
    tb.add_column("", width=5)
    for r in res:
        c = r.get("check") or {}
        b = c.get("best") or {}
        m = b.get("measured") or {}
        k = b.get("contract") or c.get("contract") or {}
        title = short(clean_title((r.get("task") or {}).get("title") or r.get("brief", "")), 44)
        mark = lambda v: NA if v is None else OK if v else BAD  # noqa: E731
        bpm = f"{mark(b.get('tempo'))} {m.get('bpm', '–')}" + (f"[dim]/{k['bpm']:g}[/]" if k.get("bpm") else "")
        key = f"{mark(b.get('key'))} {m.get('key', '–')}" + (f"[dim]/{k['key']}[/]" if k.get("key") else "")
        why = [w for w in b.get("why") or [] if w.startswith("clean:")]
        skip = [w for w in b.get("skipped") or [] if w.startswith("clean:")]
        if c.get("error"):
            cl = f"{BAD} [red]{short(c['error'], 40)}[/]"
        elif not b.get("delivered"):
            cl = f"{BAD} [red]{short((b.get('why') or ['not delivered'])[0], 40)}[/]"
        else:
            reason = (why or skip or [""])[0].replace("clean: ", "")
            cl = f"{mark(b.get('clean'))} [dim]{short(reason, 40)}[/]"
        verdict = "[bold green]PASS[/]" if strict_pass(r) else "[bold red]FAIL[/]"
        tb.add_row(r.get("worker", ""), title, bpm, key, cl, verdict)
    done = {(r.get("brief"), r.get("seed")) for r in res}
    for w, s in sorted((status.get("workers") or {}).items()):
        if s.get("state") in ("building", "verifying", "starting") and (s.get("brief"), s.get("seed")) not in done:
            tb.add_row(w, short(clean_title(s.get("title") or s.get("brief") or ""), 44),
                       "", "", f"[yellow]{s['state']}…[/]", "")
    total = status.get("total") or (state or {}).get("n") or len(res)
    n_pass = sum(strict_pass(r) for r in res)
    title = (f"workers · {run_dir.parent.name if run_dir else '—'} · {len(res)}/{total} done · "
             f"{n_pass} pass" if res or status else "workers · pending")
    if not res and not status:
        tb.add_row("", "[dim]no results yet[/]", "", "", "", "")
    return Panel(tb, title=title, title_align="left")


def ledger_panel(state: dict | None) -> Panel:
    tb = Table(box=box.SIMPLE_HEAD, expand=True, pad_edge=False)
    tb.add_column("gen", width=4)
    tb.add_column("coach hypothesis", ratio=5)
    tb.add_column("patch", ratio=2)
    tb.add_column("train", ratio=1, no_wrap=True)
    tb.add_column("kept", width=8)
    hist = (state or {}).get("history") or []
    if hist:                                         # this loop's rows (the ledger file spans every loop)
        for h in hist:
            kept = "[red]coach err[/]" if h.get("error") else "[green]✓ KEPT[/]" if h.get("kept") else "[red]✗[/]"
            tb.add_row(str(h["gen"]), short(h.get("hypothesis", ""), 160), h.get("patch_desc", ""),
                       f"{h['before']}→{h['after']}/{h['n']}", kept)
    else:
        rows = [c for c in ledger_rows() if not state or c[0] == state.get("loop")]
        for c in rows:
            kept = "[green]✓ KEPT[/]" if "KEPT" in c[6] else "[red]✗[/]"
            tb.add_row(c[1], short(c[2], 160), c[3], c[4].replace(" ", ""), kept)
        if not rows:
            tb.add_row("", "[dim]no generations yet (gen 0 is the baseline)[/]", "", "", "")
    return Panel(tb, title="ledger", title_align="left")


def bar(k: int, n: int, width: int = 32, style: str = "green") -> Text:
    fill = round(width * k / n) if n else 0
    t = Text()
    t.append("█" * fill, style=style)
    t.append("░" * (width - fill), style="dim")
    return t


def heldout_panel(state: dict | None) -> Panel:
    ho = (state or {}).get("heldout") or {}
    cur = (state or {}).get("current") or {}
    noise = (state or {}).get("noise")
    note = ho.get("note") if isinstance(ho.get("note"), str) else ""
    lines = []
    for label in ("v0", "best"):
        h = ho.get(label) if isinstance(ho.get(label), dict) else None
        name = "v0   " if label == "v0" else ("vBest" if not note else note.split()[0][:5].ljust(5))
        if h:
            n = h.get("n") or 1
            t = Text(f"{name} ")
            t.append_text(bar(h["pass"], n, style="cyan" if label == "v0" else "green"))
            t.append(f" {h['pass']}/{n}", style="bold")
            t.append(f"  {Path(h.get('version', '')).name}", style="dim")
            lines.append(t)
        elif cur.get("gen") == "held-out" and cur.get("phase") == label:
            res = results(RUNS / cur["stamp"] / cur["version"])
            total = (jload(RUNS / cur["stamp"] / "status.json", {}) or {}).get("total") or len(res) or 1
            k = sum(strict_pass(r) for r in res)
            t = Text(f"{name} ")
            t.append_text(bar(k, total, style="yellow"))
            t.append(f" running… {k} pass of {len(res)}/{total} done", style="yellow")
            lines.append(t)
        else:
            lines.append(Text(f"{name} " + "░" * 32 + " pending", style="dim"))
    foot = Text(f"noise floor: a train patch must beat the best by > {noise} pass" + ("" if noise == 1 else "es"),
                style="dim") if noise is not None else Text("noise floor: pending", style="dim")
    v0h, bh = ho.get("v0"), ho.get("best")
    if isinstance(v0h, dict) and isinstance(bh, dict):
        d = bh["pass"] - v0h["pass"]
        foot.append(f"   Δ held-out {d:+d}", style="bold green" if d > (noise or 0) else "bold yellow")
    if note:
        lines.append(Text(note, style="italic yellow"))
    return Panel(Group(*lines, foot), title="HELD-OUT  (never shown to the coach)", title_align="left")


def log_panel() -> Panel:
    lines = log_tail()
    return Panel(Text("\n".join(lines) if lines else "no loop.log yet", style="dim"), title="loop.log",
                 title_align="left")


def frame(loop: str | None):
    name = loop or latest_loop()
    state = jload(RUNS / name / "loop.json") if name else None
    return Group(header(state), workers_panel(state), ledger_panel(state), heldout_panel(state), log_panel())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="print one frame and exit")
    ap.add_argument("--loop", help="loop name (default: latest)")
    ap.add_argument("--every", type=float, default=2.0)
    ap.add_argument("--width", type=int)
    ap.add_argument("--svg", help="save one frame to this SVG file and exit")
    a = ap.parse_args()
    if a.svg:
        con = Console(record=True, width=a.width or 120, file=open("/dev/null", "w"))
        con.print(frame(a.loop))
        con.save_svg(a.svg, title="evolve watch")
        return
    con = Console(width=a.width) if a.width else Console()
    if a.once:
        con.print(frame(a.loop))
        return
    with Live(frame(a.loop), console=con, screen=True, auto_refresh=False) as live:
        try:
            while True:
                time.sleep(a.every)
                live.update(frame(a.loop), refresh=True)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
