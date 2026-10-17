#!/usr/bin/env python3
"""Brief -> contract: the tempo, key and style a beat must meet, read from what a real person stated. No LLM.

    contract(task, split=None) -> {"bpm": float | None, "key": "F#m" | None, "style": str, "refs": [audio paths]}

`task` is a bench/tasks.json row (stated_bpm / stated_key from the uploader, title, query) or a plain brief string.
Stated fields win; otherwise BPM and key are parsed from the text ("70 BPM", "tempo 90", "B minor", "key C#m", "Bmin").
Keys are normalised to <tonic><"m" if minor>, tonic spelled with sharps: "Bmin" -> "Bm", "D#minor" -> "D#m",
"Gbm" -> "F#m", "F#Major" -> "F#".
"""
from __future__ import annotations

import re

PCS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
KEY_RX = re.compile(r"^\s*([A-Ga-g])\s*([#♯b♭]?)\s*([A-Za-z]*)\s*$")
BPM_RX = re.compile(r"(\d{2,3}(?:\.\d+)?)\s*bpm|\b(?:bpm|tempo)\s*[:=]?\s*(\d{2,3}(?:\.\d+)?)", re.I)


def parse_key(s: str | None) -> tuple[int, str] | None:
    """(tonic pitch class, "major" | "minor") or None."""
    if not s:
        return None
    m = KEY_RX.match(str(s).replace("-", " "))
    if not m:
        return None
    letter, acc, mode = m.group(1).upper(), m.group(2), m.group(3)
    if mode == "m" or mode.lower() in ("min", "minor"):
        mode = "minor"
    elif mode in ("", "M") or mode.lower() in ("maj", "major"):
        mode = "major"
    else:
        return None
    pc = (PCS[letter] + (1 if acc and acc in "#♯" else -1 if acc and acc in "b♭" else 0)) % 12
    return pc, mode


def key_name(pc: int, mode: str) -> str:
    return NAMES[pc % 12] + ("m" if mode == "minor" else "")


def norm_key(s: str | None) -> str | None:
    k = parse_key(s)
    return key_name(*k) if k else None


def _key_from_text(text: str) -> str | None:
    # only trust an explicit key: "key C#m", "in B minor", "Bmin", "F# major" — not every capital letter in a title
    for m in re.finditer(r"\bkey\s*(?:of\s*)?[:=]?\s*([A-G][#♯b♭]?\s*(?:minor|min|major|maj|m)?)(?![a-z])", text, re.I):
        k = norm_key(m.group(1))
        if k:
            return k
    for m in re.finditer(r"\b([A-G][#♯b♭]?)\s*(minor|min|major|maj)\b", text, re.I):
        k = norm_key(m.group(1) + m.group(2))
        if k:
            return k
    return None


def _bpm_from_text(text: str) -> float | None:
    m = BPM_RX.search(text)
    if not m:
        return None
    v = float(m.group(1) or m.group(2))
    return v if 40 <= v <= 220 else None


def _style(task: dict | str) -> str:
    if isinstance(task, str):
        text = task
    else:
        text = task.get("query") or task.get("title") or task.get("brief") or ""
    t = re.sub(r"[\[(](?:free|sold|free for profit)[\])]", "", text, flags=re.I)
    t = re.sub(r"\btype\s*beats?\b.*$", "", t, flags=re.I) or t
    return re.sub(r"\s+", " ", t).strip(" -|\"'").lower()


def refs_for(task: dict, split: dict | None = None, n: int = 4) -> list[str]:
    """Reference audio to compare against: the brief's own YouTube upload first, then up to n other uploads of the
    same style from the same side of the split (train briefs never point at held-out audio, and vice versa)."""
    out = [task["audio"]] if task.get("audio") else []
    if split:
        side = next((v for v in split.values() if isinstance(v, list) and any(
            isinstance(x, dict) and x.get("id") == task.get("id") for x in v)), [])
        out += [x["audio"] for x in side if x.get("id") != task.get("id") and x.get("audio")
                and task.get("style") and x.get("style") == task.get("style")][:n]
    return out


def contract(task: dict | str, split: dict | None = None) -> dict:
    text = task if isinstance(task, str) else " ".join(
        str(task.get(k) or "") for k in ("title", "brief", "description", "query"))
    bpm = None if isinstance(task, str) else task.get("stated_bpm")
    key = None if isinstance(task, str) else norm_key(task.get("stated_key"))
    return {"bpm": float(bpm) if bpm else _bpm_from_text(text),
            "key": key or _key_from_text(text),
            "style": _style(task),
            "refs": [] if isinstance(task, str) else refs_for(task, split)}


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path
    tasks = json.loads((Path(__file__).resolve().parents[1] / "bench" / "tasks.json").read_text())
    for t in tasks if len(sys.argv) < 2 else [" ".join(sys.argv[1:])]:
        print(json.dumps(contract(t), ensure_ascii=False))
