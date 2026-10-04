# Real-API spend

Count `meta.total_usd` only for runs that called a provider.
A run is excluded when every agent model is `mock` or `scripted`
and `insider_driver` is not `llm`. Mock goldens and scripted pilots
do not move this total.

- Warning line: $80.
- Hard flag: $100. That is the team cap tonight. Stop real calls at this line.

## Current tally

- Source: `(no runs directory)`.
- Real-API runs: 0.
- Excluded mock or scripted-only runs: 0.
- Total USD: 0.00.
- Status: OK: $0.00 is under the $80 warning line.

Refresh with `python -m coop.eval.spend RUNS_DIR --write research/spend.md`.
