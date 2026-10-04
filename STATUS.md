# Status

Live checklist for the submission. Tick items as they land, and log the reasoning behind each choice in [DECISIONS.md](DECISIONS.md).

**Deadline: Sunday, Oct 4, 2026, 9:00 AM ET (8:00 AM CT).** Late work cannot win. Hacking began Sat Oct 3, 1:00 PM ET.

Last updated: Sun Oct 4, 2026.

## Check-ins (CT)

| When | Question |
| --- | --- |
| Sat 4:17 PM | Are the domain, question, measurable outcome, and data locked? |
| Sat 10:03 PM | Is a discovery loop running yet? |
| Sun 6:04 AM | Time to switch to the demo, the videos, and this checklist. |
| Sun 7:16 AM | Final call: is everything on app.hack-nation.ai and the Google form? |

## 1. Foundations (first 4 hours, now overdue)

- [x] Science domain chosen (research cooperative of LLM agents; ground truth from the simulator)
- [x] Question chosen and testable in 24 hours
- [x] Bottleneck named (first member to shift the group off the charter)
- [x] Measurable outcome and baseline defined (state violations vs an all-honest policy)
- [x] Data access confirmed (the simulator generates the traces; no external dataset)
- [x] Domain, question, and data written into `README.md` and `AGENTS.md`

## 2. Lab setup

- [x] Repo, Python 3.12, and Omnigent 0.16 environment (`labenv`)
- [x] Agent bundle: PI, literature, experimenter, critic (`config.yaml`, `pi.md`, `agents/`)
- [x] Model split: PI, literature, and experimenter on Claude; critic on Codex (GPT)
- [x] Auth through CLI subscriptions, Anthropic API key as fallback only
- [x] Critic prompt in `agents/critic/critic.md` with APPROVE / REVISE / NEEDS HUMAN APPROVAL verdicts
- [x] `DECISIONS.md` started
- [ ] `omnigent run . -p "Say hello"` works end to end
- [ ] Human approval gate on `sys_os_shell` fires under the `claude-sdk` harness
- [ ] Critic confirmed to reach GPT, not Claude
- [ ] Decide whether the critic gets read-only access to `experiments/` and `research/`
- [ ] Omnigent policies and tool permissions enforce the human approval boundary
- [ ] Shared research record, so every decision can be reconstructed
- [ ] Planner has a budget and chooses between competing tests
- [ ] Independent searches or experiments run in parallel

## 3. Discovery loop (next 14 hours)

- [ ] At least two candidate tests, one chosen on expected learning, feasibility, and cost
- [ ] One full loop: question, evidence, hypothesis, experiment, result, updated decision
- [ ] The result changes the next decision
- [ ] Critic run against a real result, with any tuning logged in `DECISIONS.md`
- [ ] Measured improvement over the baseline (1.5x, 3x, 5x, or 10x)

## 4. Write-up (final 6 hours)

- [ ] Cited evidence for factual claims, with source evidence or run records attached
- [ ] Agent-generated hypotheses labeled as such, uncertainty preserved
- [ ] Controls and human approval gates documented
- [ ] Validation still needed before real-world use stated
- [ ] Next experiment named and justified by what the lab learned
- [ ] Auth path actually used noted in the write-up
- [ ] Agent specs and policies documented

## 5. Submission

- [ ] GitHub repo is **public**
- [ ] Live demo deployed (Vercel, Replit, or Lovable)
- [ ] Demo video (about two minutes)
- [ ] Tech video
- [ ] Team video
- [ ] Every teammate has an app.hack-nation.ai account and is added under "team & submission"
- [ ] Challenge 3 named in Discord
- [ ] Submitted on [app.hack-nation.ai](https://app.hack-nation.ai)
- [ ] Submitted on the backup Google form
- [ ] Optional: LinkedIn post tagging Hack-Nation by the deadline (Go Viral prize)
