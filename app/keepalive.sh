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
    # producer: OpenAI (AGENT_PROVIDER=openai, key from ~/logicagent/.env); Crusoe only for the coach loop
    (cd "$ROOT" && env -u CRUSOE_PRODUCER -u CRUSOE_PRODUCER_MODEL MILES_SELF_CHECK=1 AGENT_PROVIDER=openai \
      OPENAI_AGENT_MODEL="${OPENAI_AGENT_MODEL:-gpt-6-sol}" \
      MILES_STUDIO_DATA="$ROOT/evolve/work/ui" nohup "$PY" server/app.py --port 8851 >> "$ROOT/evolve/work/ui/server.out" 2>&1 &)
  fi
  # the MCP pool: more studios, same producer env, own data dirs (never 886x / work/w*)
  for pd in 8853:mcp1 8854:mcp2; do
    P=${pd%%:*}; D="$ROOT/evolve/work/${pd##*:}"
    if ! up $P api/state; then
      echo "$(date) starting pool studio :$P"
      mkdir -p "$D"; [ -f "$D/MILES.md" ] || cp "$ROOT/evolve/versions/v0/MILES.md" "$D/MILES.md"
      (cd "$ROOT" && env -u CRUSOE_PRODUCER -u CRUSOE_PRODUCER_MODEL MILES_SELF_CHECK=1 AGENT_PROVIDER=openai \
        OPENAI_AGENT_MODEL="${OPENAI_AGENT_MODEL:-gpt-6-sol}" \
        MILES_STUDIO_DATA="$D" nohup "$PY" server/app.py --port $P >> "$D/server.out" 2>&1 &)
    fi
  done
  if ! up 8850 api/ledger; then
    echo "$(date) starting page :8850"
    (cd "$ROOT/demo" && nohup "$PY" server.py --port 8850 >> "$ROOT/demo/server.log" 2>&1 &)
  fi
  sleep 10
done
