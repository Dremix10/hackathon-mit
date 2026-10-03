# Decisions

Short ledger. Newest date last. Open items are the only unfinished work.

## Open

- Run `omnigent run . -p "Say hello"`. On that session, note the auth path used and that the critic session is Codex (GPT), not Claude.
- After the first real result, check the critic's verdict is useful. Tune `agents/critic/critic.md` here if it rubber-stamps or rejects everything.

## 2026-10-03

- Experimenter uses `claude-sdk`. PI and literature do too. Critic uses `codex` (GPT), so the reviewer is a different vendor from the work it checks.
- Auth is the Claude and Codex CLI subscriptions from `omnigent setup`. An Anthropic API key is fallback only. No OpenAI API key.
- Critic prompt is `agents/critic/critic.md`. Verdicts: APPROVE, REVISE, or NEEDS HUMAN APPROVAL. Anything it could not check goes under Unverified.
- Critic may `sys_os_read` only under `experiments/` and `research/`. Shell, write, and edit are denied.
- Experimenter commands ask a human first. The gate matches `sys_os_shell` (what `claude-sdk` actually calls) and `Bash`.
- `labenv` is a symlink to `~/.local/share/hackathon-mit/labenv`. A real env in this directory breaks `omnigent run .` (symlinks, and bundles over 10,000 files). Do not create one here.
- PI sandbox may write `/tmp/claude-501`. On this Mac the Claude CLI uses that path; Omnigent's default temp grant does not cover it, so startup returns EPERM.
