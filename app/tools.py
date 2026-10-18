"""Miles's steps as separate tools (plan → reference → build → check → revise), plus analyze / list / stems.

Each tool is fn(args, emit) -> result dict; emit(label, text, detail="", status="done") posts a step to the job's live
log (the DAW's ?job= panel and the MCP server's progress notifications). demo/server.py runs them as jobs.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.parse
import uuid
from pathlib import Path

import core
from evolve import check, contract

HERE = Path(__file__).resolve().parent
REFS = core.CACHE / "refs"                  # downloaded reference clips (never pushed, never played)
PLANS_FILE = core.CACHE / "plans.json"
SONGS = core.DATA / "songs"
LIVE = core.CACHE / "live.json"


def _load(f: Path, default):
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return default


def daw(song_id: str | None, v=None) -> str | None:
    return f"/?song={song_id}" + (f"&v={v}" if v is not None else "") if song_id else None


# ------------------------------------------------------------------ plan
def cookbook_lookup(q: str) -> list[tuple[str, dict]]:
    book = _load(core.ROOT / "server/cookbook.json", {})
    q, out = q.lower(), []
    for name, p in book.items():
        words = name.split()
        if name in q:
            out.append((len(name) + 10, name, p))
        elif len(words) > 1 and sum(w in q for w in words) >= len(words) - 1:
            out.append((len(name), name, p))
    return [(n, p) for _, n, p in sorted(out, key=lambda x: -x[0])]


INSTRUMENTS = ["piano", "rhodes", "keys", "guitar", "strings", "808", "synth", "sax", "flute", "choir", "bass",
               "organ", "bells", "brass", "vocal", "harp"]


def plan_beat(a: dict, emit) -> dict:
    brief = str(a.get("brief") or "").strip()
    b = core.brief(brief)
    emit("Reading the brief", f"“{brief[:120]}”")
    hits = cookbook_lookup(brief)
    genre = None
    if hits:
        name, p = hits[0]
        genre = {"name": name, "typical_bpm": p.get("bpm_median"), "bpm_range": p.get("bpm_range"),
                 "common_keys": (p.get("keys") or [])[:5]}
        emit("Looking up the genre", f"{name}: typical ~{p.get('bpm_median')} BPM, keys {', '.join(genre['common_keys'])}")
    bpm = b["bpm"] or (genre or {}).get("typical_bpm") or 90
    if not b["bpm"]:
        emit("Decision", f"No tempo in the brief → {bpm:g} BPM" + (f" (typical for {genre['name']})" if genre else ""))
    key = b["key"] or ((genre or {}).get("common_keys") or [None])[0] or "Am"
    if not b["key"]:
        emit("Decision", f"No key in the brief → {key}")
    inst = ["drums", "bass"] + ([i for i in INSTRUMENTS if i in brief.lower() and i != "bass"] or ["sample"])
    plan = {"plan_id": "p_" + uuid.uuid4().hex[:6], "brief": brief, "bpm": float(bpm), "key": key,
            "mood": [m for m in b["mood"] if m != "—"] + [w for w in ("rainy", "night", "late-night", "lofi", "dusty", "jazzy",
                                                                 "melancholic", "summer", "sunny") if w in brief.lower() and w not in b["mood"]], "length": b["length"] or 30, "instruments": inst,
            "genre": genre, "style": b.get("style")}
    plans = _load(PLANS_FILE, {})
    plans[plan["plan_id"]] = plan
    PLANS_FILE.write_text(json.dumps(dict(list(plans.items())[-200:])))
    emit("Spec", f"{plan['bpm']:g} BPM · {key} · {' · '.join(plan['mood']) or '—'} · {plan['length']} s · "
                 f"{', '.join(inst)}")
    return plan


# ------------------------------------------------------------------ measure
def measure(path: Path) -> dict:
    import numpy as np
    y = check.load_any(path)
    if y is None:
        return {"error": f"can't read {path.name}"}
    sr = 22050
    t = check.measure_tempo(y)
    import librosa
    k = check.measure_key(librosa.effects.harmonic(y))
    rms = float(np.sqrt(np.mean(y.astype("float64") ** 2)) or 1e-9)
    return {"seconds": round(len(y) / sr, 1), "bpm": t.get("bpm"), "key": k.get("key"),
            "key_runner_up": k.get("runner_up"), "loudness_db_rms": round(20 * np.log10(rms), 1),
            "peak_db": round(20 * np.log10(float(np.max(np.abs(y))) or 1e-9), 1)}


def youtube(query: str, emit, seconds: int = 60) -> dict:
    REFS.mkdir(parents=True, exist_ok=True)
    url = query if query.startswith(("http://", "https://")) else f"ytsearch1:{query}"
    emit("Searching YouTube", query[:100], status="running")
    meta = subprocess.run(["yt-dlp", "-J", "--no-playlist", "--no-warnings", url], capture_output=True, text=True,
                          timeout=60)
    info = json.loads(meta.stdout or "{}")
    if info.get("entries"):
        info = info["entries"][0]
    if not info.get("id"):
        raise RuntimeError("no YouTube result: " + (meta.stderr or "")[-200:])
    vid, title = info["id"], info.get("title") or query
    emit("Found", f"{title} ({info.get('duration_string') or '?'}) · youtu.be/{vid}")
    out = REFS / f"{vid}.m4a"
    if not out.exists():
        subprocess.run(["yt-dlp", "-q", "--no-warnings", "--no-playlist", "-f", "bestaudio[ext=m4a]/bestaudio",
                        "--download-sections", f"*0-{seconds}", "-o", str(REFS / f"{vid}.%(ext)s"),
                        "--remux-video", "m4a", f"https://www.youtube.com/watch?v={vid}"],
                       capture_output=True, text=True, timeout=180)
    got = next(iter(sorted(REFS.glob(f"{vid}.*"))), None)
    if not got:
        raise RuntimeError("download failed (YouTube may have blocked it)")
    emit("Downloaded", f"first {seconds} s of audio → {got.name} (not played)")
    return {"title": title, "url": f"https://www.youtube.com/watch?v={vid}", "path": str(got),
            "channel": info.get("channel"), "duration": info.get("duration")}


def find_reference(a: dict, emit) -> dict:
    ref = youtube(str(a.get("query") or ""), emit)
    emit("Listening", "measuring tempo, key and loudness", status="running")
    m = measure(Path(ref["path"]))
    emit("Measured", f"{m.get('bpm')} BPM · {m.get('key')} · {m.get('loudness_db_rms')} dB RMS")
    return ref | {"measured": m}


def resolve_audio(src: str, emit) -> Path:
    if src.startswith(("http://localhost", "http://127.0.0.1")):
        p = urllib.parse.urlparse(src).path
        if p.startswith("/track/"):
            sid, _, f = urllib.parse.unquote(p[len("/track/"):]).partition("/")
            return sdir(sid) / f
        if p.startswith("/cache/"):
            return core.CACHE / urllib.parse.unquote(p[len("/cache/"):])
    if src.startswith(("http://", "https://")):
        return Path(youtube(src, emit)["path"])
    if re.fullmatch(r"s_[0-9a-f]+(:\d+)?", src):
        sid, _, v = src.partition(":")
        d = sdir(sid)
        wavs = sorted((p for p in d.glob("v*.wav") if "_" not in p.stem), key=lambda p: int(p.stem[1:] or 0))
        return next((p for p in wavs if v and p.stem == f"v{v}"), wavs[-1] if wavs else d / "missing.wav")
    return Path(src).expanduser()


def analyze_audio(a: dict, emit) -> dict:
    p = resolve_audio(str(a.get("path_or_url") or ""), emit)
    if not p.exists():
        raise RuntimeError(f"no such audio: {p}")
    emit("Listening", p.name, status="running")
    m = measure(p)
    emit("Measured", f"{m.get('bpm')} BPM · {m.get('key')} · {m.get('loudness_db_rms')} dB RMS · {m.get('seconds')} s")
    return {"path": str(p)} | m


# ------------------------------------------------------------------ build / check / revise
def spec_of(a: dict) -> dict:
    if a.get("plan_id"):
        p = _load(PLANS_FILE, {}).get(a["plan_id"])
        if p:
            return p
    s = a.get("spec") or {}
    if isinstance(s, str):
        s = {"brief": s}
    if not s.get("brief") and a.get("brief"):
        s["brief"] = a["brief"]
    b = core.brief(s.get("brief") or "")
    return {"brief": s.get("brief") or "", "bpm": s.get("bpm") or b["bpm"], "key": s.get("key") or b["key"],
            "mood": s.get("mood") or b["mood"], "length": s.get("length") or b["length"]}


def sdir(sid: str) -> Path:
    return core.song_dir(sid) or SONGS / sid


def latest_version(sid: str) -> tuple[int, Path | None]:
    wavs = sorted((p for p in sdir(sid).glob("v*.wav") if "_" not in p.stem), key=lambda p: int(p.stem[1:] or 0))
    return (int(wavs[-1].stem[1:]), wavs[-1]) if wavs else (0, None)


WORDS = {"make_songs": "Building", "edit_songs": "Repairing", "verify_songs": "Self-check", "rank_songs": "Ranking",
         "pick": "Keeping", "study_reference": "Listening", "genre_profile": "Genre", "find_records": "Digging records",
         "arrange": "Arranging", "remember": "Noting", "plan": "Plan"}


def follow(run, emit):
    """Run a backend job (run(note) -> out) while streaming the producer's own steps (sample picked, repairs,
    self-checks, arrangement) to emit as they happen. Returns (out, steps)."""
    import threading
    box, port, seen, steps = {}, {}, set(), []

    def note(label, text):
        if label == "_skip":                                 # steps already in the session before this job
            seen.update(text.split(","))
            seen.add("_")
            return
        if label == "Studio":
            port["p"] = int(text.rsplit(":", 1)[-1])
        emit(label, text, status="running" if label.startswith("Waiting") else "done")

    th = threading.Thread(target=lambda: box.update(out=run(note)), daemon=True)
    th.start()
    while th.is_alive() or not seen:
        th.join(1.5)
        if not port.get("p"):
            if not th.is_alive():
                break
            continue
        try:
            st = core.pool.call(port["p"], "GET", "/api/state", None, 5)[1] or {}
        except Exception:                                    # noqa: BLE001
            continue
        for x in st.get("steps") or []:
            key = (x.get("id"), x.get("status"))
            if x.get("status") == "running" or key in seen or x.get("id") in seen:
                continue
            seen.add(key)
            seen.add(x.get("id"))
            label = WORDS.get(x.get("tool"), (x.get("tool") or "").replace("_", " ").capitalize())
            line = (x.get("title") or "") + (f" — {x['detail']}" if x.get("detail") else "")
            steps.append(f"{label}: {line}"[:220])
            emit(label, (x.get("title") or "")[:160], (x.get("detail") or "")[:200])
        if not th.is_alive():
            break
    return box.get("out") or {}, steps


def build_beat(a: dict, emit) -> dict:
    spec = spec_of(a)
    ref = a.get("reference")
    text = spec["brief"] or "a beat"
    extra = [f"{spec['bpm']:g} BPM" if spec.get("bpm") else "", spec.get("key") or ""]
    text = f"{text} — make 1 track like that, exactly {' '.join(x for x in extra if x)}"
    if ref:
        text += f". Reference to study: {ref}"
    emit("Building", text[:160], status="running")
    out, steps = follow(lambda note: core.job(text, f"build · {time.strftime('%H:%M:%S')}", 420, note), emit)
    songs = [s for s in out.get("songs") or [] if s.get("audio")]
    if not songs:                                           # a render that died: one more try before giving up
        why = (out.get("error") or (out.get("reply") or "")[:200] or "no audio came back").replace("\n", " ")
        emit("Decision", f"that take didn't render ({why[:120]}) → trying once more", status="error")
        out, more = follow(lambda note: core.job(text, f"build retry · {time.strftime('%H:%M:%S')}", 420, note), emit)
        steps += more
        songs = [s for s in out.get("songs") or [] if s.get("audio")]
    if not songs:
        raise RuntimeError("Miles couldn't deliver a song: " + (out.get("error") or (out.get("reply") or "")[:300]
                                                                 or "no audio came back"))
    s = songs[0]
    v = int(Path(s["audio"]).stem[1:] or 0)
    emit("Built", f"{s['title']} · {s.get('bpm')} BPM · {s.get('key')} · v{v} in {out.get('seconds', 0):.0f} s")
    return {"song_id": s["id"], "v": v, "title": s["title"], "audio": s["audio"], "seconds": out.get("seconds"),
            "steps": steps, "parts": s.get("parts") or [], "bpm": s.get("bpm"), "key": s.get("key"),
            "spec": spec, "daw": daw(s["id"], v), "reply": (out.get("reply") or "")[:400],
            "url": f"/track/{s['id']}/{Path(s['audio']).name}"}


def check_beat(a: dict, emit) -> dict:
    sid = str(a.get("song_id") or "")
    spec = spec_of(a)
    d = sdir(sid)
    if not d.is_dir():
        raise RuntimeError(f"no such song {sid}")
    c = contract.contract({"brief": spec.get("brief") or "", "stated_bpm": spec.get("bpm"),
                           "stated_key": spec.get("key"), "title": spec.get("brief") or ""})
    emit("Self-check", f"measuring {sid} against {c.get('bpm') or '—'} BPM · {c.get('key') or '—'}", status="running")
    r = check.check(d, c, a.get("seconds"))
    m = r.get("measured") or {}
    mk = lambda k: "✓" if r.get(k) else "✗" if r.get(k) is False else "·"
    emit("Measured", f"tempo {m.get('bpm')} {mk('tempo')} · key {m.get('key')} {mk('key')} · clean {mk('clean')} · "
                     f"delivered {mk('delivered')}", "; ".join(r.get("why") or []),
         status="done" if r.get("pass") else "error")
    v, _ = latest_version(sid)
    return {"song_id": sid, "v": v, "pass": r.get("pass"), "checks": {k: r.get(k) for k in ("delivered", "tempo", "key", "clean")},
            "asked": {"bpm": c.get("bpm"), "key": c.get("key")}, "measured": {"bpm": m.get("bpm"), "key": m.get("key")},
            "why": r.get("why") or [], "skipped": r.get("skipped") or [], "daw": daw(sid, v)}


def revise_beat(a: dict, emit) -> dict:
    sid, instr = str(a.get("song_id") or ""), str(a.get("instruction") or "").strip()
    meta = _load(sdir(sid) / "song.json", {})
    if not meta:
        raise RuntimeError(f"no such song {sid}")
    v0, _ = latest_version(sid)
    emit("Revising", f"{meta.get('title')} (v{v0}): “{instr[:120]}”", status="running")
    port = core.port_of(sid)

    def run(note):
        lease = core.acquire(lambda l, t: note(l, t), only=port)
        try:
            if meta.get("session"):
                try:
                    core.pool.call(port, "POST", f"/api/sessions/{meta['session']}/open", {}, 15)
                except Exception:                                # noqa: BLE001
                    pass
            try:
                old = core.pool.call(port, "GET", "/api/state", None, 5)[1].get("steps") or []
                note("_skip", ",".join(x.get("id") or "" for x in old))
            except Exception:                                    # noqa: BLE001
                pass
            note("Studio", f"working on :{port}")
            return core.pool.call(port, "POST", "/api/task", {"text": instr, "song_id": sid, "timeout": 400}, 430)[1]
        finally:
            lease.release()
    out, steps = follow(run, emit)
    v, audio = latest_version(sid)
    emit("Revised", f"v{v0} → v{v} · " + ((out or {}).get("reply") or "")[:160].replace("\n", " "))
    res = check_beat({"song_id": sid, "spec": a.get("spec") or {"brief": meta.get("brief") or meta.get("title") or ""}},
                     emit)
    return res | {"from_v": v0, "title": meta.get("title"), "url": f"/track/{sid}/{audio.name}" if audio else None,
                  "steps": steps, "instruction": instr, "reply": ((out or {}).get("reply") or "")[:300]}


# ------------------------------------------------------------------ catalogue
def list_beats(a: dict, emit) -> dict:
    n = int(a.get("limit") or 10)
    items = _load(LIVE, []) + _load(core.CACHE / "gallery.json", [])
    out = []
    for b in items[:n]:
        out.append({"title": b.get("title"), "bpm": b.get("bpm"), "key": b.get("key"), "pass": b.get("pass"),
                    "checks": b.get("checks"), "song_id": b.get("song_id"), "v": b.get("v"),
                    "daw": daw(b.get("song_id"), b.get("v")), "url": b.get("url"), "source": b.get("source")})
    emit("Catalogue", f"{len(out)} beats, {sum(1 for b in out if b['pass'])} pass every check")
    return {"beats": out}


def get_stems(a: dict, emit) -> dict:
    sid, v = str(a.get("song_id") or ""), a.get("v")
    if v is None:
        v, _ = latest_version(sid)
    d = sdir(sid)
    stems = {r: f for r in ("drums", "bass", "sample") if (f := d / f"v{v}_{r}.wav").exists()}
    note = None
    if not stems:                                           # arranged versions don't write stems: use the newest that did
        for vv in range(int(v), -1, -1):
            stems = {r: f for r in ("drums", "bass", "sample") if (f := d / f"v{vv}_{r}.wav").exists()}
            if stems:
                note = f"v{v} is an arranged render without stems; these are v{vv}'s loop stems"
                v = vv
                break
    if not stems:
        raise RuntimeError(f"no stems for {sid}")
    emit("Stems", f"{sid} v{v}: {', '.join(stems)}" + (f" ({note})" if note else ""))
    return {"song_id": sid, "v": v, "note": note, "daw": daw(sid, v),
            "stems": {r: {"path": str(f), "url": f"/track/{sid}/{f.name}"} for r, f in stems.items()}}


TOOLS = {"plan_beat": plan_beat, "find_reference": find_reference, "build_beat": build_beat, "check_beat": check_beat,
         "revise_beat": revise_beat, "analyze_audio": analyze_audio, "list_beats": list_beats, "get_stems": get_stems}
