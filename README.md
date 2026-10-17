# miles-evolve

Miles makes beats from a one-line brief ("Frank Ocean piano ballad type beat, 70 BPM, B minor"). Today we gave it a
deterministic four-check self-test — **Delivered**, **Tempo**, **Key** measured from the audio, and **Clean** via
Miles's own ear rules — and a **Nemotron-on-Crusoe coach** that reads the failures and rewrites Miles's own playbook,
one patch per generation. A patch is kept only if the train pass rate beats the best so far by more than the noise
floor. The proof is the **held-out** pass rate: briefs the coach never sees.

Miles itself (the studio server, producer agent and audio engine) is private. This repo is the harness that drives it
over HTTP (`POST /api/task` on N scratch studios), plus the one server-side change we made today, as a diff.

## Architecture

```
 brief ──► contract.py ──► pool.py ───────────────────────► check.py ──► results JSON
 (title,   (BPM, key,      N scratch studios in parallel    Delivered     evolve/runs/<stamp>/<version>/
  stated    style, refs)   (one data dir + port each),      Tempo         <brief>-s<seed>.json
  BPM/key)                 producer = Nemotron on Crusoe    Key  (audio)        │
                           (crusoe_shim.py)                 Clean (ear)         ▼
                                                                          coach.py (Nemotron on Crusoe)
          ┌───────────────────────────────────────────────────────────── reads train failures only,
          ▼                                                                writes ONE patch + hypothesis
   versions/<loop>/gN  =  best + patch   (playbook rule, addendum rule, or an allowed env flag)
          │
          ▼
   run.py gate: re-score on train → keep iff after > best + noise ──► ledger.md row, view.py
          │
          ▼
   run.py heldout: v0 vs a candidate on the held-out split (16 briefs × 1 seed)
```

- **contract.py** — brief → `{bpm, key, style, refs}` from what the uploader actually stated. No LLM.
- **check.py** — the self-test, measured from the rendered audio. Tempo from the drums (half/double OK), key by
  Krumhansl–Kessler on CQT chroma of the pitched stems (relative major/minor OK), Clean = Miles's ear finds no off-key
  or detuned bass, off-grid sample or missing part. A check that can't be graded is skipped, never a free pass at the
  gate: `run.py` counts a pass only if Clean was graded.
- **pool.py** — N worker studios at once, each a fresh session with the version's playbook rewritten before every job.
- **crusoe_shim.py** — swaps the producer's LLM calls for Crusoe Managed Inference (OpenAI-compatible, tools), same
  return shapes; on only with `CRUSOE_PRODUCER=1`.
- **coach.py / llm.py** — the coach proposes one patch: a playbook/addendum rule or a flag from `flags_allowed.json`
  (self-check on, best-of-N takes, which Crusoe model plays the producer).
- **run.py** — the resumable loop, the gate and the ledger; `heldout` scores v0 vs the best on the held-out split.
- **view.py** — `evolve watch`, a read-only live terminal view of all of the above.
- **w5_verify_songs.diff** — the server change: a `verify_songs` tool so the producer can run `check.py` on its own
  songs and repair with `edit_songs` (max 2 rounds) before saying done. Off by default (`MILES_SELF_CHECK=1`).
- **split.json** — 32 briefs that state BPM and key, split by channel and stratified by style: 16 train / 16 held-out.

## Results

Loop `loop-20261017-141624` (4 workers, 16 train briefs × 1 seed, noise floor 1 pass; the two v0 noise runs agreed
with about 0 flips once a checker bug on revised beats was fixed). Six generations; `evolve/ledger.md` is the record. Coach spend for the whole loop: $0.048.

| gen | coach hypothesis (Nemotron on Crusoe) | patch | train before → after | kept |
|---|---|---|---|---|
| 0 | baseline v0 | — | 8/16 | — |
| 1 | Miles ignores accidentals (#/b) and mode when it reads the brief's key | `MILES.md` addendum +1 rule | 8 → 8 | ✗ |
| 2 | Miles doesn't check its own output, so key and tempo errors go uncorrected | flag `MILES_SELF_CHECK=1` | 8 → 8 | ✗ |
| 3 | bass lines lean on the fifth, so key detection hears the wrong tonic | addendum +1 rule | 8 → 6 | ✗ |
| 4 | loops start on the V chord, so the dominant is heard as the tonic | addendum +1 rule | 8 → 8 | ✗ |
| 5 | beats default to A, so transpose out of it | addendum +1 rule | 8 → 5 | ✗ |
| 6 | Miles swaps a requested minor key for its relative major | addendum +1 rule | 8 → 5 | ✗ |

No patch beat v0 by more than the noise floor, so the gate shipped nothing, and three patches (gens 3, 5, 6) would
have made Miles worse. That is the gate working: a change that doesn't measurably help never reaches the producer.

Held-out (running): v0 vs the gen-1 patch on the 16 held-out briefs, 1 seed. g1 was rejected on train; it is tested
on held-out for transparency, not as a shipped version.

| held-out (16 briefs × 1 seed) | pass |
|---|---|
| v0 | **TBD** /16 |
| g1 (rejected on train) | **TBD** /16 |

## Built today vs pre-existing Miles

| pre-existing Miles (private, not in this repo) | built today (this repo) |
|---|---|
| headless studio server, `POST /api/task` | Crusoe Nemotron producer shim (`crusoe_shim.py`) |
| producer agent | brief → contract parser + four-check self-test (`contract.py`, `check.py`) |
| engine and renders | parallel worker pool of scratch studios (`pool.py`) |
| YouTube tools (`collect.py`, `study_reference`, `find_records`) | `verify_songs` producer step (`w5_verify_songs.diff`, off by default) |
| `server/ear.py` (the ear rules behind Clean) | new briefs + train / held-out split (`collect_briefs.py`, `make_split.py`, `split.json`) |
| `eval/key_eval.py` | coach + gate + resumable loop + ledger (`coach.py`, `llm.py`, `run.py`, `ledger.md`) |
| `bench/tasks.json` | live watch view (`view.py`) |

## Run

The real loop needs a Miles checkout around `evolve/` (`server/app.py`, the engine, the reference audio), which is
private. The offline dry run at the bottom needs nothing but Python: it fakes the pool and the coach so you can see
the loop, gate, ledger and view work end to end.

```sh
# the Crusoe key: CRUSOE_API_KEY in the environment or in .env.crusoe (gitignored, never printed)
evolve/RUN_LOOP.sh                       # 6 generations, 4 workers on ports 8861-8864, log in evolve/runs/loop.log
python evolve/run.py --gens 6 --resume   # pick up where it stopped
python evolve/run.py heldout --seeds 1 --candidate g1   # v0 vs a candidate on held-out
python evolve/view.py                    # watch it live (needs rich: pip install rich); --once prints one frame
EVOLVE_FAKE_POOL=1 EVOLVE_FAKE_LLM=1 python evolve/run.py --gens 3   # offline dry run, no Miles, no key
```

`evolve/samples/` has the loop state and three per-brief result files from the gen-0 run, to show the formats.

## Judge UI + MCP (`app/`)

A one-page showcase (auto-playing review gallery of real renders, each with its four self-check badges measured from
the audio, plus the coach rounds from `evolve/ledger.md`) and a stdio MCP server with one tool, `make_music(brief)`,
over the same backend. See [app/README.md](app/README.md).

```bash
claude mcp add miles -- ~/logicagent/.venv/bin/python <repo>/app/mcp_server.py
```
