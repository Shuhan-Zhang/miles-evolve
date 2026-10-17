# Evolve ledger

One row per generation. Train split only; the coach never sees held-out. A patch is kept only if train passes beat the best by more than the noise floor.

| loop | gen | hypothesis (coach) | patch | train before → after | noise | kept | ear |
|---|---|---|---|---|---|---|---|
| loop-20261017-141624 | 1 | Miles often ignores accidentals (#/b) and mode when parsing the key from the brief, causing the written key to be off by a semitone or to default to the relative major/minor. | addendum +1 rule | 8/16 → 8/16 | 1 | ✗ |  |
