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

Refresh the tally above with `python -m coop.eval.spend RUNS_DIR --write research/spend.md` only when you mean to replace this file. The batch ledger below is the running team total the driver reads. Warn above $80. Hard-stop at $100.

## Batch ledger

Running team total is the sum of actual_usd. Warn above $80. Hard-stop at $100.

| when | kind | runs | model | estimated_usd | actual_usd | team_total_usd | note |
| --- | --- | --- | --- | --- | --- | --- | --- |
