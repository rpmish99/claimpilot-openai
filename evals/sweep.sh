#!/usr/bin/env bash
# Threshold sweep + single-model baselines on the full labeled set (2 runs each).
set -u
cd "$(dirname "$0")/.."
PY=.venv/bin/python
run() { $PY evals/run.py --runs 2 --workers 6 "$@"; }
run --tag "cascade t=0.70" --threshold 0.70 > evals/results/cascade_t070.log 2>&1 &
run --tag "cascade t=0.80" --threshold 0.80 > evals/results/cascade_t080.log 2>&1 &
wait
run --tag "cascade t=0.90" --threshold 0.90 > evals/results/cascade_t090.log 2>&1 &
run --tag "cascade t=0.95" --threshold 0.95 > evals/results/cascade_t095.log 2>&1 &
wait
run --tag "luna only" --extraction-model gpt-6-luna --resolution-model gpt-6-luna --escalation-model none > evals/results/luna_only.log 2>&1 &
run --tag "sol only" --extraction-model gpt-6.1-sol --resolution-model gpt-6-luna --escalation-model none > evals/results/sol_only.log 2>&1 &
wait
echo SWEEP DONE
