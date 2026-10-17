#!/usr/bin/env python3
"""Self-test: does a rendered song meet its brief's contract? Measured from the audio, no LLM, deterministic.

    check(song_dir, contract, seconds=None) -> {tempo, key, clean, delivered, pass, why, measured}
    ~/logicagent/.venv/bin/python evolve/check.py data/songs/s_729a58 [--brief "key C#m; tempo 146"] [--json]
    ~/logicagent/.venv/bin/python evolve/check.py <song dir> --task <split.json id>   # that brief's contract + refs

  delivered  song.json says ready, its mix is on disk, and (when `seconds` is given) it came back within 180 s.
  tempo      the tempo measured from the drums (else the mix) is within 6 % of the contract's BPM, half or double
             counting (bench/run.py:tempo_ok), or the pulse at the contract's BPM is as strong as at the measured one
             (triplet hats fool the tracker). The song's own `bpm` field is reported, never trusted.
  key        the key measured from the pitched stems (sample layers + bass; the mix when there are no stems) by
             Krumhansl-Kessler profiles on CQT chroma equals the contract's key: same tonic and mode, or its relative
             major / minor.
  clean      server/ear.py finds nothing (ear.verdict): no off-key or detuned bass, no off-grid sample, no part the
             brief asked for missing, no stretch mismatch.
  ref        (when the contract lists reference audio: the brief's YouTube upload + same-style uploads) similarity
             of the mix to the nearest one, 0..1, over tempo feel, spectral shape, brightness, low end and busyness,
             with the biggest gaps in words. Advisory: it fails the song only when the contract sets `ref_min`.
A check that can't be graded (no stated BPM / key, a version without stems for clean) is None, listed in
`skipped` with the reason, and doesn't fail the song; `pass` needs every graded
check True. `why` lists each failure in a line a coach can act on.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ear  # noqa: E402
from contract import NAMES, contract as make_contract, key_name, parse_key  # noqa: E402

SR = ear.SR
TEMPO_TOL = 0.06
TEMPO_SUPPORT = 0.95
DEADLINE = 180.0
# Krumhansl-Kessler key profiles (C major / C minor)
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def tempo_ok(ours: float | None, ref: float | None) -> bool | None:
    if not ours or not ref:
        return None
    return any(abs(ours - r) / r <= TEMPO_TOL for r in (ref, ref / 2, ref * 2))


def song_files(song_dir: Path) -> tuple[dict, Path | None, dict[str, Path]]:
    """(song.json, the delivered version's mix, that version's stems). The engine writes stems only for a fresh
    render: an arranged / revised version (v1.wav, v2.wav…) has a mix and no stems, so stems can come back empty."""
    s = json.loads((song_dir / "song.json").read_text())
    vs = s.get("versions") or [{}]
    cur = s.get("current")
    v = vs[cur] if isinstance(cur, int) and 0 <= cur < len(vs) else vs[-1]
    mix = song_dir / Path(v["audio_url"]).name if v.get("audio_url") else None
    stems = {r: song_dir / Path(u).name for r, u in (v.get("stems") or {}).items()}
    return s, mix, {r: p for r, p in stems.items() if p.exists()}


def ear_check(song_dir: Path, s: dict, stems: dict[str, Path]) -> dict:
    """server/ear.py's check on exactly these stems (ear.check itself reads the last version's)."""
    old_songs, old_paths = ear.SONGS, ear.song_paths
    ear.SONGS, ear.song_paths = song_dir.parent, lambda sid: (s, stems)
    try:
        return ear.check(song_dir.name)
    finally:
        ear.SONGS, ear.song_paths = old_songs, old_paths


def measure_tempo(y: np.ndarray, want: float | None = None) -> dict:
    """{"bpm": the beat tracker's tempo, "support": how strong the pulse is at the brief's tempo (or half / double)
    relative to the tracker's pick, in the mean autocorrelation tempogram}. Trap hats in triplets pull the tracker to
    2/3 of the tempo; support >= TEMPO_SUPPORT says the brief's pulse is there just as strongly."""
    import librosa
    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=ear.HOP)
    if env.max() <= 0:
        return {"bpm": None}
    tempo = getattr(librosa.feature, "tempo", None) or librosa.beat.tempo          # librosa < 0.10
    bpm = round(float(tempo(onset_envelope=env, sr=SR, hop_length=ear.HOP)[0]), 1)
    out = {"bpm": bpm}
    if want:
        tg = librosa.feature.tempogram(onset_envelope=env, sr=SR, hop_length=ear.HOP).mean(axis=1)
        f = librosa.tempo_frequencies(len(tg), sr=SR, hop_length=ear.HOP)

        def at(b: float) -> float:
            sel = (f >= b * (1 - TEMPO_TOL)) & (f <= b * (1 + TEMPO_TOL))
            return float(tg[sel].max()) if sel.any() else 0.0
        mine = at(bpm)
        out["support"] = round(max(at(want), at(want / 2), at(want * 2)) / mine, 2) if mine > 0 else None
    return out


def measure_key(y: np.ndarray) -> dict:
    import librosa
    tuning = librosa.estimate_tuning(y=y, sr=SR)
    chroma = librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=ear.HOP * 2, tuning=tuning,
                                        fmin=librosa.note_to_hz("C1"), n_octaves=7)
    c = chroma.sum(axis=1)
    if c.max() <= 0:
        return {}
    scores = []
    for pc in range(12):
        for mode, prof in (("major", MAJOR), ("minor", MINOR)):
            scores.append((float(np.corrcoef(c, np.roll(prof, pc))[0, 1]), pc, mode))
    scores.sort(reverse=True)
    r, pc, mode = scores[0]
    return {"key": key_name(pc, mode), "r": round(r, 3), "margin": round(r - scores[1][0], 3),
            "runner_up": key_name(scores[1][1], scores[1][2]), "tuning_c": round(float(tuning) * 100)}


# --- reference comparison: how close is the beat to real uploads of the style? (advisory unless contract["ref_min"])
REF_CACHE = Path(__file__).resolve().parent / "cache" / "ref_feats.json"
FFMPEG = next((p for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg")
               if Path(p).exists()), "ffmpeg")


def load_any(p: Path) -> np.ndarray | None:
    """Mono SR float audio from a wav or a YouTube download (m4a / webm, through ffmpeg)."""
    try:
        y = ear.load(p)
        if y is not None:
            return y
    except Exception:                                                   # noqa: BLE001
        pass
    import subprocess
    r = subprocess.run([FFMPEG, "-v", "quiet", "-i", str(p), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True)
    y = np.frombuffer(r.stdout, dtype=np.float32)
    return y.copy() if len(y) > SR else None


def fold(bpm: float) -> float:
    while bpm >= 140:
        bpm /= 2
    while bpm < 70:
        bpm *= 2
    return bpm


def features(y: np.ndarray, middle: float | None = None) -> dict:
    """What a type-beat listener compares, in numbers: tempo feel, spectral shape, brightness, low end, busyness."""
    import librosa
    if middle and len(y) > middle * SR:                                # a reference: its middle, past the intro
        a = (len(y) - int(middle * SR)) // 2
        y = y[a: a + int(middle * SR)]
    mel = librosa.feature.melspectrogram(y=y, sr=SR, n_mels=40, hop_length=ear.HOP * 2)
    prof = np.log10(mel.mean(axis=1) + 1e-10)
    spec = np.abs(librosa.stft(y, n_fft=2048, hop_length=ear.HOP * 2)) ** 2
    f = librosa.fft_frequencies(sr=SR, n_fft=2048)
    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=ear.HOP)
    ons = librosa.onset.onset_detect(onset_envelope=env, sr=SR, hop_length=ear.HOP)
    tempo = getattr(librosa.feature, "tempo", None) or librosa.beat.tempo
    return {"bpm": round(fold(float(tempo(onset_envelope=env, sr=SR, hop_length=ear.HOP)[0])), 1),
            "shape": [round(float(x), 3) for x in prof - prof.mean()],
            "centroid": round(float(np.median(librosa.feature.spectral_centroid(S=np.sqrt(spec), sr=SR)))),
            "low": round(float(spec[f < 120].sum() / (spec.sum() + 1e-12)), 3),
            "onsets": round(len(ons) / (len(y) / SR), 2)}


def ref_features(path: str) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    k = f"{p}|{p.stat().st_mtime_ns}"
    try:
        cache = json.loads(REF_CACHE.read_text())
    except (OSError, ValueError):
        cache = {}
    if k in cache:
        return cache[k]
    y = load_any(p)
    if y is None:
        return None
    feats = features(y, middle=60.0)
    try:                                                               # parallel workers: write-then-rename
        cache = {**(json.loads(REF_CACHE.read_text()) if REF_CACHE.exists() else {}), k: feats}
        REF_CACHE.parent.mkdir(exist_ok=True)
        tmp = REF_CACHE.with_suffix(f".{__import__('os').getpid()}.tmp")
        tmp.write_text(json.dumps(cache))
        tmp.replace(REF_CACHE)
    except (OSError, ValueError):
        pass
    return feats


def similarity(a: dict, b: dict) -> dict:
    sa, sb = np.array(a["shape"]), np.array(b["shape"])
    cos = float(sa @ sb / (np.linalg.norm(sa) * np.linalg.norm(sb) + 1e-9))
    return {"tempo": float(np.exp(-abs(np.log(a["bpm"] / b["bpm"])) / 0.06)),
            "timbre": max(0.0, cos),
            "brightness": float(np.exp(-abs(np.log(a["centroid"] / max(b["centroid"], 1))) / 0.4)),
            "low_end": float(np.exp(-abs(a["low"] - b["low"]) / 0.15)),
            "busyness": float(np.exp(-abs(np.log((a["onsets"] + .1) / (b["onsets"] + .1))) / 0.5))}


def compare_refs(y: np.ndarray, refs: list[str]) -> dict:
    """{score 0..1 against the nearest reference, mean over refs, per-dimension sims, gaps in words}."""
    ours = features(y)
    rows = []
    for r in refs:
        f = ref_features(r)
        if f:
            sims = similarity(ours, f)
            rows.append((float(np.mean(list(sims.values()))), Path(r).stem, sims, f))
    if not rows:
        return {"score": None, "why": "no reference audio on disk"}
    best = max(rows, key=lambda x: x[0])
    med = {k: float(np.median([f[k] for *_, f in rows])) for k in ("bpm", "centroid", "low", "onsets")}
    dims = {k: round(float(np.mean([s[k] for _, _, s, _ in rows])), 2) for k in rows[0][2]}
    words = {"tempo": f"tempo feel {ours['bpm']:g} vs the refs' {med['bpm']:g}",
             "timbre": "spectral shape unlike the refs",
             "brightness": f"{'darker' if ours['centroid'] < med['centroid'] else 'brighter'} than the refs "
                           f"(centroid {ours['centroid']} vs {med['centroid']:.0f} Hz)",
             "low_end": f"{'less' if ours['low'] < med['low'] else 'more'} low end than the refs "
                        f"({ours['low'] * 100:.0f} % vs {med['low'] * 100:.0f} % under 120 Hz)",
             "busyness": f"{'sparser' if ours['onsets'] < med['onsets'] else 'busier'} than the refs "
                         f"({ours['onsets']:g} vs {med['onsets']:g} hits/s)"}
    gaps = [words[k] for k, v in sorted(dims.items(), key=lambda x: x[1]) if v < 0.5][:2]
    return {"score": round(best[0], 3), "nearest": best[1], "mean": round(float(np.mean([r[0] for r in rows])), 3),
            "n_refs": len(rows), "dims": dims, "gaps": gaps, "ours": {k: v for k, v in ours.items() if k != "shape"}}


def key_ok(heard: str | None, want: str | None) -> bool | None:
    w, h = parse_key(want), parse_key(heard)
    if not w:
        return None
    if not h:
        return False
    if w == h:
        return True
    rel = ((w[0] + 3) % 12, "major") if w[1] == "minor" else ((w[0] - 3) % 12, "minor")
    return h == rel


def check(song_dir: str | Path, contract: dict | str | None = None, seconds: float | None = None) -> dict:
    song_dir = Path(song_dir)
    out = {"song": song_dir.name, "tempo": None, "key": None, "clean": None, "delivered": False, "pass": False,
           "why": [], "skipped": [], "measured": {}}
    try:
        s, mix, stems = song_files(song_dir)
    except (OSError, ValueError, KeyError) as e:
        out["why"].append(f"not delivered: no readable song ({type(e).__name__})")
        return out
    c = contract if isinstance(contract, dict) else make_contract(contract or s.get("brief") or "")
    out["contract"] = c
    m = out["measured"]
    m.update({"claimed_bpm": s.get("bpm"), "claimed_key": s.get("key"), "status": s.get("status")})
    ready = s.get("status") == "ready" and mix is not None and mix.exists()
    late = seconds is not None and seconds > DEADLINE
    out["delivered"] = bool(ready and not late)
    if not ready:
        out["why"].append(f"not delivered: status {s.get('status')}" + ("" if mix and mix.exists() else ", no mix"))
        return out
    if late:
        out["why"].append(f"not delivered in time: {seconds:.0f} s > {DEADLINE:.0f} s")

    y_mix = ear.load(mix)
    ys = {r: ear.load(p) for r, p in stems.items()}
    ys = {r: y for r, y in ys.items() if y is not None}

    # tempo, from the drums (else the mix)
    src = ys.get("drums") if ys.get("drums") is not None else y_mix
    t = measure_tempo(src, c.get("bpm")) if src is not None else {}
    m["bpm"], m["bpm_support"] = t.get("bpm"), t.get("support")
    out["tempo"] = tempo_ok(m["bpm"], c.get("bpm"))
    if out["tempo"] is False and (t.get("support") or 0) >= TEMPO_SUPPORT:
        out["tempo"] = True
    if out["tempo"] is False:
        out["why"].append(f"tempo: measured {m['bpm']:g} BPM, brief says {c['bpm']:g}"
                          + (f" (song claims {s['bpm']:g})" if s.get("bpm") else ""))

    # key, from the pitched stems (the mix's harmonic part when the version has no stems)
    pitched = [y for r, y in ys.items() if r.startswith("sample") or r == "bass"]
    if pitched:
        n = max(len(y) for y in pitched)
        harm = sum(np.pad(y, (0, n - len(y))) for y in pitched)
    else:
        import librosa
        harm = librosa.effects.harmonic(y_mix) if y_mix is not None else None
    k = measure_key(harm) if harm is not None else {}
    m["key"], m["key_r"], m["key_margin"], m["key_runner_up"] = (k.get("key"), k.get("r"), k.get("margin"),
                                                                 k.get("runner_up"))
    out["key"] = key_ok(k.get("key"), c.get("key"))
    if out["key"] is False:
        out["why"].append(f"key: heard {k.get('key')}, brief says {c['key']}"
                          + (f" (song claims {s['key']})" if s.get("key") else ""))

    # clean: Miles' own ear on the delivered version's stems; a version without stems can't be graded
    m["stems"] = sorted(stems)
    if not stems:
        out["skipped"].append("clean: the delivered version has no stems (arranged / revised renders don't write "
                              "them)")
    else:
        try:
            r = ear_check(song_dir, s, stems)
            bad = ear.verdict(r)
            m["ear"] = {x: r.get(x) for x in ("bass", "sample", "drums", "balance", "missing")}
        except Exception as e:                                           # noqa: BLE001
            bad = [f"ear failed: {type(e).__name__}: {str(e)[:80]}"]
        out["clean"] = not bad
        out["why"] += [f"clean: {b}" for b in bad]
    if out["tempo"] is None:
        out["skipped"].append("tempo: the brief states no BPM" if not c.get("bpm") else "tempo: nothing measurable")
    if out["key"] is None:
        out["skipped"].append("key: the brief states no key")

    # reference: the brief's own upload + same-style uploads (advisory; gates only with contract["ref_min"])
    if c.get("refs") and y_mix is not None:
        try:
            out["ref"] = compare_refs(y_mix, c["refs"])
        except Exception as e:                                           # noqa: BLE001
            out["ref"] = {"score": None, "why": f"{type(e).__name__}: {str(e)[:80]}"}
        rs = out["ref"].get("score")
        if c.get("ref_min") is not None and rs is not None and rs < c["ref_min"]:
            out["ref"]["pass"] = False
            out["why"].append(f"ref: {rs:.2f} < {c['ref_min']} vs the nearest upload; "
                              + "; ".join(out["ref"].get("gaps") or []))
        elif rs is not None:
            out["ref"]["pass"] = c.get("ref_min") is None or rs >= c["ref_min"]

    graded = [out[x] for x in ("tempo", "key", "clean") if out[x] is not None]
    if (out.get("ref") or {}).get("pass") is False:
        graded.append(False)
    out["pass"] = bool(out["delivered"] and all(graded))
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    brief = sys.argv[sys.argv.index("--brief") + 1] if "--brief" in sys.argv else None
    if brief:
        args = [a for a in args if a != brief]
    if "--task" in sys.argv:                                   # a split.json brief by id: its contract + references
        tid = sys.argv[sys.argv.index("--task") + 1]
        args = [a for a in args if a != tid]
        split = json.loads((Path(__file__).resolve().parent / "split.json").read_text())
        task = next(x for v in split.values() if isinstance(v, list) for x in v
                    if isinstance(x, dict) and x.get("id") == tid)
        brief = make_contract(task, split)
    rows = []
    for d in args:
        r = check(d, brief)
        rows.append(r)
        if "--json" in sys.argv:
            continue
        mark = {True: "✓", False: "✗", None: "·"}
        mm = r["measured"]
        print(f"{r['song']:10} {'PASS' if r['pass'] else 'FAIL'}  tempo {mark[r['tempo']]} {mm.get('bpm')}  "
              f"key {mark[r['key']]} {mm.get('key')}  clean {mark[r['clean']]}  delivered {mark[r['delivered']]}  "
              f"| want {r.get('contract', {}).get('bpm')} {r.get('contract', {}).get('key')}"
              + (f"  ref {r['ref'].get('score')} ~{r['ref'].get('nearest')}" if r.get("ref") else ""))
        for w in r["why"]:
            print("    -", w)
        for w in r.get("skipped") or []:
            print("    · skipped", w)
    if "--json" in sys.argv:
        print(json.dumps(rows, indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
