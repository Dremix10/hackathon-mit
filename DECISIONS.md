# Decisions

A short dated log of the choices that shaped this lab. Each entry says what we decided, why, and what is next. Newest at the bottom.

## 2026-10-03: Experimenter runs on Claude, critic stays on Codex

**Decided.** The experimenter uses the `claude-sdk` harness instead of `codex`. The PI, literature, and experimenter agents all run on Claude. The critic is the only agent on a different vendor (`codex`, GPT).

**Why.** The critic should be independent of the work it reviews, and able to oppose it. A reviewer that shares a model family with the author tends to share its blind spots, so it agrees too easily. The experimenter is the agent whose code and results the critic judges most, so it has to be on the other side of the vendor line from the critic.

**Next.**
- Make the critic's prompt explicitly adversarial: try to refute the hypothesis, not just review it.
- Check that the human approval gate on `sys_os_shell` fires under the `claude-sdk` harness in a test session.
- Confirm in the first run that the critic really reaches GPT and not Claude.

## 2026-10-03: Run on CLI subscriptions, API key as fallback only

**Decided.** Agents authenticate through the Claude and Codex CLI subscriptions that `omnigent setup` configures. An Anthropic API key is kept as a backup for when the Claude CLI cannot be used. No OpenAI API key.

**Why.** Subscriptions are already paid for, so a 24-hour run has no surprise per-token bill. Omnigent supports both CLI logins directly. It also strips `ANTHROPIC_API_KEY` from the Claude CLI's environment on purpose so the subscription is used, which means a stray key will not silently start charging.

**Next.** Anyone reproducing this needs a Claude subscription and a ChatGPT subscription (for Codex), or their own keys. This is noted in the README under Setup. Note which auth path was actually used in the final write-up.

## 2026-10-03: Critic is an adversarial guardian that treats other agents as untrusted

**Decided.** The critic's prompt now casts it as the lab's guardian. It treats the PI, literature, and experimenter as untrusted: possibly mistaken, corner-cutting, or ill-intentioned. It must verify claims against evidence (URLs, commands, seeds, output paths) instead of accepting self-reports, and it tries to break every hypothesis and result before approving.

**Why.** An agent that reviews politely tends to rubber-stamp what it is shown. A zero-trust stance forces it to look for fabricated citations, leakage, missing controls, and unsafe commands, which are the failures that would invalidate a result. Pairing it with a different vendor (see the first entry) makes the independence real and not only claimed.

**Next.**
- The critic has no `os_env`, so it may only see what the PI passes it. Decide whether to give it read-only access to `experiments/` and `research/` so it can verify outputs itself.
- Watch for the opposite failure: a critic that rejects everything. If it blocks useful work, tune the prompt and record what changed here.

## 2026-10-03: Critic prompt moved to `critic.md` and cut down

**Decided.** The critic's prompt now lives in `agents/critic/critic.md` and `config.yaml` points at it with `instructions: critic.md`. The prompt is about half the length. It keeps the four checks (evidence, rigor, safety, scope) and drops the "guardian" framing, the "ill-intentioned" language, and "oppose before you agree". It now says to raise only issues it can point to, and it has a fixed reply format: APPROVE, REVISE, or NEEDS HUMAN APPROVAL, with issues, what was checked, and what was unverified.

**Why.** The old prompt told the critic to "check that URLs exist and match", but the critic has no `os_env` and sees only what the PI passes it, so it could not do that. It also pushed toward rejecting everything, which is the failure mode the previous entry warned about. The new prompt asks only for what the critic can do, and it says so when it can't check something.

**Next.**
- Still open: give the critic read-only access to `experiments/` and `research/` so it can verify outputs itself.
- Run it against a real result and see whether the verdicts are useful. Record any tuning here.
