#!/bin/bash
# Keep the judge page (:8850) and its Miles backend (:8851) up until $UNTIL (epoch s). Detached: nohup demo/keepalive.sh &
# Never touches :8820 or evolve/work/w*.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$HOME/logicagent/.venv/bin/python"
UNTIL=${UNTIL:-$(( $(date +%s) + 6*3600 ))}
up() { curl -s -o /dev/null -m 3 "http://127.0.0.1:$1/$2"; }
while [ "$(date +%s)" -lt "$UNTIL" ]; do
  if ! up 8851 api/state; then
    echo "$(date) starting backend :8851"
    (cd "$ROOT" && env $(python3 -c "import json;d=json.load(open('evolve/versions/v0/flags.json'));d['MILES_SELF_CHECK']='1';print(' '.join(f'{k}={v}' for k,v in d.items()))") \
      MILES_STUDIO_DATA="$ROOT/evolve/work/ui" nohup "$PY" server/app.py --port 8851 >> "$ROOT/evolve/work/ui/server.out" 2>&1 &)
  fi
  if ! up 8850 api/ledger; then
    echo "$(date) starting page :8850"
    (cd "$ROOT/demo" && nohup "$PY" server.py --port 8850 >> "$ROOT/demo/server.log" 2>&1 &)
  fi
  sleep 10
done
