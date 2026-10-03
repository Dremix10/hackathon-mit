# Agentic Scientific Discovery

24-hour lab for Challenge 3 of the 7th Global AI Hackathon (Hack-Nation × Databricks Omnigent, with the MIT clubs). Build specialist agents that run one scientific discovery loop and let the result change the next experiment.

**Submit by Sunday, Oct 4, 2026, 9:00 AM ET.** Late work cannot win.

Read these (the original PDFs have been removed from the repo):

- [Challenge brief](docs/challenge-brief.md)
- [Kickoff notes](docs/kickoff.md)

## Setup

Python 3.12 is pinned in `.python-version`. Dependencies, including Omnigent 0.16, are installed in a virtualenv that lives outside this directory. `labenv` in the repo is only a symlink to it. `omnigent run .` packs this whole directory into the agent bundle, and the server rejects symlinks and bundles over 10,000 files. A real virtualenv here fails that check (`labenv/bin/python3` is a link, and the env has far more than 10,000 files).

```bash
source labenv/bin/activate
omnigent setup
omnigent run . -p "Say hello"
```

Credentials: the lab runs on CLI subscriptions, not API keys. `omnigent setup` signs in to a Claude subscription (PI, literature, experimenter) and a ChatGPT subscription through the Codex CLI (critic). An Anthropic API key is only a fallback for when the Claude CLI cannot be used. Never commit keys.

Recreate the env later with [uv](https://docs.astral.sh/uv/). Keep the real env outside the repo, then point `labenv` at it:

```bash
mkdir -p "$HOME/.local/share/hackathon-mit"
UV_PROJECT_ENVIRONMENT="$HOME/.local/share/hackathon-mit/labenv" uv sync
ln -sfn "$HOME/.local/share/hackathon-mit/labenv" labenv
```

Omnigent has to orchestrate the live workflow. Managed Databricks is optional. Docs: [install](https://omnigent.ai/quickstart/install) · [GitHub](https://github.com/omnigent-ai/omnigent) · [Databricks quickstart](https://developers.databricks.com/docs/omnigent/quickstart)

## Still open

The science question is not chosen yet. It needs a measurable outcome, data you can actually reach today, and a result that can change the next decision. See the brief for the loop and the scoring weights (Omnigent orchestration is 30%).

## Submit

Public GitHub repo, live demo (Vercel, Replit, or Lovable), plus demo, tech, and team videos. Upload to [app.hack-nation.ai](https://app.hack-nation.ai) and the backup Google form. Details are in the kickoff notes.
