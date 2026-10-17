# Evolve ledger

One row per generation. Train split only; the coach never sees held-out. A patch is kept only if train passes beat the best by more than the noise floor.

| loop | gen | hypothesis (coach) | patch | train before → after | noise | kept | ear |
|---|---|---|---|---|---|---|---|
| loop-20261017-141624 | 1 | Miles often ignores accidentals (#/b) and mode when parsing the key from the brief, causing the written key to be off by a semitone or to default to the relative major/minor. | addendum +1 rule | 8/16 → 8/16 | 1 | ✗ |  |
| loop-20261017-141624 | 2 | Miles does not self-check its output, so key and tempo errors go uncorrected. | flag MILES_SELF_CHECK=1 | 8/16 → 8/16 | 1 | ✗ |  |
| loop-20261017-141624 | 3 | Miles's bass lines often emphasize the fifth scale degree, causing automatic key detection to hear a key a fourth/fifth away from the intended tonic. | addendum +1 rule | 8/16 → 6/16 | 1 | ✗ |  |
| loop-20261017-141624 | 4 | Miles often starts loops on the V chord, causing key detectors to hear the dominant as the tonic. | addendum +1 rule | 8/16 → 8/16 | 1 | ✗ |  |
| loop-20261017-141624 | 5 | Miles frequently leaves the generated music in a default key (roughly A minor/A major) rather than transposing to the brief’s specified key, so the detected key often disagrees with the brief. | addendum +1 rule | 8/16 → 5/16 | 1 | ✗ |  |
