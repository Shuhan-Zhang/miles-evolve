# Evolve ledger

One row per generation. Train split only; the coach never sees held-out. A patch is kept only if train passes beat the best by more than the noise floor.

| loop | gen | hypothesis (coach) | patch | train before → after | noise | kept | ear |
|---|---|---|---|---|---|---|---|
