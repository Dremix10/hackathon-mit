# hackathon-mit

Challenge 3 of the 7th Global AI Hackathon: Agentic Scientific Discovery. Omnigent must orchestrate the live loop. Submission deadline is Sunday, Oct 4, 2026, 9:00 AM ET.

The Omnigent bundle is this directory: `config.yaml`, `pi.md`, and `agents/`. `pi.md` is the PI prompt. This file is for coding agents, not the lab.

Read `docs/challenge-brief.md` and `docs/kickoff.md` before changing the lab. The original PDFs are no longer in the repo; those docs are the source of truth.

Do not commit secrets. The science question is chosen: can earned trust, perceived privacy, and group membership recruit LLM agents into violating and concealing violations of their original mandate, does that practice persist after the instigator leaves, and does an initiated agent initiate a newcomer? Ground truth is simulator state in `coop/sim/`, not agent summaries. Do not invent a different domain or dataset. The shared contract is `coop/schema.py`. Analysis of run logs lives in `coop/analysis/`; the pre-specified rules are in `coop/analysis/RULES.md`. Contract changes go in `DECISIONS.md`.

Agent prompts live in `.md` files (`pi.md`, `agents/critic/critic.md`) and are referenced from `config.yaml` with `instructions:`. Keep them short and only ask agents to do what their tools allow.

When you change how the lab works (agent prompts, harnesses, policies, auth), append a dated entry to `DECISIONS.md`: what changed, why, and what is next.
