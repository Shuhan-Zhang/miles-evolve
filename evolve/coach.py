#!/usr/bin/env python3
"""The coach: reads the train failures of the current best version and writes ONE patch, with a one-line hypothesis.

    patch = coach.propose(best_dir, results, history)   # -> {"hypothesis", "kind", ...}
    coach.apply(patch, best_dir, new_dir)                # new version folder = best + the patch

A patch is one of:
  {"kind": "addendum", "text": "<one rule>"}                       one line appended to addendum.md
  {"kind": "playbook", "text": "<one rule>", "replace": "<old line>"?}   a MILES.md rule, added or replacing a line
  {"kind": "flag", "flag": "MILES_SELF_CHECK", "value": "1"}       an env flag for the worker processes
Flags the coach may touch are listed in FLAGS (override with evolve/flags_allowed.json). The coach only ever sees
train results: run.py never passes it held-out briefs.
"""
from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

try:
    from evolve import llm
except ImportError:                                  # run as a script from evolve/
    import llm  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
FLAGS = {
    "MILES_SELF_CHECK": {"values": ["0", "1"],
                         "what": "1 adds a verify_songs step: Miles self-tests its songs against the brief "
                                 "(tempo, key, clean) and may repair them with edit_songs up to twice before saying done"},
    "MILES_BEST_OF": {"values": ["1", "2", "3"], "what": "how many takes the engine renders per part, keeping the best"},
}
_allowed = ROOT / "evolve/flags_allowed.json"
if _allowed.exists():
    FLAGS = json.loads(_allowed.read_text())
MAX_FAILS = 24
MAX_BOOK = 6000

SYSTEM = """You are the coach of Miles, an AI music producer that turns a one-line brief ("Frank Ocean piano ballad \
type beat, 70 BPM, B minor") into a beat, using tools and a playbook (MILES.md) it reads into its system prompt.
Every beat is self-tested with no LLM: Delivered (a song comes back ready within 180 s), Tempo (measured BPM within 6% \
of the brief, half/double ok), Key (key measured from the audio matches the brief, relative major/minor ok), Clean \
(Miles's ear finds no off-key bass, off-beat sample, missing part or bad mix; a revised beat without stems can't be \
graded and counts as a fail). A brief passes only if all four pass. Failures may also carry "refs": an advisory \
similarity to real reference uploads with plain-language gaps; use them to choose between fixes, not as the target.

Your job: read the failures, find the ONE most common root cause you can fix, and write ONE small patch. A patch that \
does not raise the pass rate on a re-run is thrown away, so aim at the biggest failing check, and do not repeat a \
hypothesis that was already rejected. Rules must be concrete instructions Miles can follow with its tools, not \
wishes ("Before writing bass, read the sample's detected key and write the bass in that key", not "make better bass").

Answer with only a JSON object:
{"hypothesis": "<one line: why beats fail>", "kind": "addendum" | "playbook" | "flag",
 "text": "<the rule, for addendum/playbook>", "replace": "<exact existing MILES.md line to replace, optional>",
 "flag": "<flag name, for kind flag>", "value": "<flag value>"}"""


def failures(results: list[dict]) -> list[dict]:
    out = []
    for r in results:
        chk = r.get("check") or {}
        best = chk.get("best") or {}
        if r.get("pass") and best.get("clean") is True:
            continue
        m = best.get("measured") or {}
        failed = [k for k in ("delivered", "tempo", "key", "clean") if best.get(k) is False]
        if best.get("delivered") and best.get("clean") is None:
            failed.append("clean not graded (revised beat has no stems: counts as a fail)")
        out.append({"brief": r.get("text") or r.get("brief"),
                    "failed": failed or (["error"] if r.get("error") or chk.get("error") else []),
                    # advisory similarity to reference uploads, with plain-language gaps ("brighter than refs")
                    "refs": {k: best["ref"].get(k) for k in ("score", "gaps")} if isinstance(best.get("ref"), dict) else None,
                    "why": (best.get("why") or [])[:5] or [r.get("error") or chk.get("error") or "unknown"],
                    "measured": {k: m.get(k) for k in ("bpm", "key", "claimed_bpm", "claimed_key") if m.get(k)},
                    "seconds": r.get("seconds"),
                    # W5: with MILES_SELF_CHECK=1 the task result carries Miles's own verify steps; none = it skipped
                    "self_checks": len(r["verify"]) if isinstance(r.get("verify"), list) else None})
    return out


def tally(results: list[dict]) -> dict:
    c = Counter()
    for r in results:
        b = ((r.get("check") or {}).get("best") or {})
        for k in ("delivered", "tempo", "key", "clean"):
            c[k] += b.get(k) is False
        c["clean_not_graded"] += bool(b.get("delivered")) and b.get("clean") is None
    return {"runs": len(results), "pass": sum(bool(r.get("pass")) and ((r.get("check") or {}).get("best") or {}).get("clean") is True
                                              for r in results), "failed_checks": dict(c)}


def prompt(version: Path, results: list[dict], history: list[dict]) -> list[dict]:
    version = Path(version)
    book = (version / "MILES.md").read_text() if (version / "MILES.md").exists() else ""
    add = (version / "addendum.md").read_text() if (version / "addendum.md").exists() else ""
    flags = json.loads((version / "flags.json").read_text()) if (version / "flags.json").exists() else {}
    if len(book) > MAX_BOOK:
        book = book[:MAX_BOOK] + "\n…(truncated)"
    hist = "\n".join(f"- gen {h['gen']}: \"{h['hypothesis']}\" ({h['kind']}) train {h['before']}→{h['after']} "
                     f"{'KEPT' if h['kept'] else 'rejected'}" for h in history) or "(none yet)"
    flag_menu = "\n".join(f"- {k} = {flags.get(k, '(unset)')}; allowed {v['values']}: {v['what']}"
                          for k, v in FLAGS.items())
    fails = failures(results)[:MAX_FAILS]
    user = f"""## Current playbook (MILES.md)
{book or '(empty)'}

## Added rules (addendum.md)
{add.strip() or '(none)'}

## Flags you may change
{flag_menu}

## Train results of this version
{json.dumps(tally(results))}

## Failures (train only)
{json.dumps(fails, indent=1)}

## Earlier patches
{hist}

Write ONE patch as JSON."""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def validate(p: dict, version: Path) -> dict:
    kind = p.get("kind")
    if not str(p.get("hypothesis") or "").strip():
        raise ValueError("no hypothesis")
    if kind in ("addendum", "playbook"):
        t = str(p.get("text") or "").strip()
        if not t or len(t) > 600:
            raise ValueError("rule text missing or over 600 chars")
        here = "".join((Path(version) / n).read_text() for n in ("MILES.md", "addendum.md") if (Path(version) / n).exists())
        if t.lstrip("- ") in here:
            raise ValueError("that rule is already in the playbook; write a different one")
        out = {"hypothesis": p["hypothesis"].strip(), "kind": kind, "text": t}
        if kind == "playbook" and p.get("replace"):
            book = (Path(version) / "MILES.md").read_text() if (Path(version) / "MILES.md").exists() else ""
            if p["replace"].strip() and p["replace"].strip() in book:
                out["replace"] = p["replace"].strip()
        return out
    if kind == "flag":
        f, v = str(p.get("flag")), str(p.get("value"))
        if f not in FLAGS or v not in FLAGS[f]["values"]:
            raise ValueError(f"flag {f}={v} not allowed; allowed: {json.dumps({k: x['values'] for k, x in FLAGS.items()})}")
        cur = json.loads((Path(version) / "flags.json").read_text()) if (Path(version) / "flags.json").exists() else {}
        if str(cur.get(f, "")) == v:
            raise ValueError(f"{f} is already {v} in this version; change something else")
        return {"hypothesis": p["hypothesis"].strip(), "kind": "flag", "flag": f, "value": v}
    raise ValueError(f"unknown kind {kind!r}")


def propose(version: Path, results: list[dict], history: list[dict], tries: int = 3) -> dict:
    msgs = prompt(version, results, history)
    err = None
    for _ in range(tries):
        raw = llm.chat_json(msgs)
        try:
            return validate(raw, version)
        except ValueError as e:
            err = e
            msgs = msgs + [{"role": "assistant", "content": json.dumps(raw)},
                           {"role": "user", "content": f"That patch is invalid: {e}. Write one valid patch as JSON."}]
    raise RuntimeError(f"coach gave no valid patch: {err}")


def apply(p: dict, base: Path, new: Path) -> Path:
    base, new = Path(base), Path(new)
    if new.exists():
        shutil.rmtree(new)
    shutil.copytree(base, new)
    if p["kind"] == "addendum":
        f = new / "addendum.md"
        old = f.read_text().rstrip() if f.exists() else ""
        f.write_text((old + "\n" if old else "") + "- " + p["text"].lstrip("- ") + "\n")
    elif p["kind"] == "playbook":
        f = new / "MILES.md"
        book = f.read_text() if f.exists() else ""
        if p.get("replace"):
            book = book.replace(p["replace"], p["text"], 1)
        else:
            book = book.rstrip() + "\n- " + p["text"].lstrip("- ") + "\n"
        f.write_text(book)
    elif p["kind"] == "flag":
        f = new / "flags.json"
        flags = json.loads(f.read_text()) if f.exists() else {}
        flags[p["flag"]] = p["value"]
        f.write_text(json.dumps(flags, indent=1) + "\n")
    (new / "patch.json").write_text(json.dumps({**p, "parent": base.name}, indent=1) + "\n")
    return new


def describe(p: dict) -> str:
    if p["kind"] == "flag":
        return f"flag {p['flag']}={p['value']}"
    return f"{p['kind']} {'~1 line' if p.get('replace') else '+1 rule'}"
