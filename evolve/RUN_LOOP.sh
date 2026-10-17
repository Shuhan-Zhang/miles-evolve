#!/bin/zsh
# Start the improvement loop in the background (4 workers on 8861-8864). Log: evolve/runs/loop.log
# The Crusoe key comes from ~/miles-hackathon/.env.crusoe: llm.py (coach) and evolve/crusoe_shim.py (each worker) read it.
cd ~/miles-hackathon || exit 1
[ -f .env.crusoe ] || [ -n "$CRUSOE_API_KEY" ] || { echo "no Crusoe key: put CRUSOE_API_KEY=... in .env.crusoe"; exit 1; }
mkdir -p evolve/runs
POOL_BASE_PORT=8860 nohup ~/logicagent/.venv/bin/python -u evolve/run.py --gens 6 --workers 4 --noise ${NOISE:-1} "$@" > evolve/runs/loop.log 2>&1 &
echo "loop started (pid $!); tail -f evolve/runs/loop.log"
