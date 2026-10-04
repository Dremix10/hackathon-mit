You choose the next cooperative-experiment grid. You do not spend API credits.

Read research/protocol.md, research/record.md, and research/pending_batch.json if it exists. Do not open sealed.json. Run:

`python -m coop.batch candidates --out research/plan.md`

That command prints two candidate tests with expected learning, feasibility, estimated USD, and a score. The order is fixed: P0 free checks, then the 10-run pilot (dev seeds, earned access, deliverable-only, scripted insider, one model and temperature; 3 recruiter, 3 null, 4 pressure-only), then the sizing step. The pilot is never confirmatory. The confirmatory batch is three equal groups on held-out seeds, not a five-arm Latin square. You may reject its choice only by naming which of learning, feasibility, and cost favors the other candidate, and you still name both. You cannot raise the $3 cap or COOP_BUDGET_USD, and you cannot pick an exploratory spend before the pilot is reported.

`n_per_group` is `floor((100 - pilot_spend - 10) / (3 * measured $/run))`. Do not fill that integer until `python -m coop.batch size` reads a positive actual in research/spend.md. After the pilot, freeze with `python -m coop.batch freeze` (it calls `coop.eval.record.log_preregistered_tests`) before any seed at or above 1000.

Write research/plan.md with both candidates, the scores, the one you pick, and what result would make you switch. Stay inside the remaining COOP_BUDGET_USD. Do not run `execute` and do not pass `--human-approved`.
