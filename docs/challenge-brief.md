# Agentic Scientific Discovery — Challenge Brief

7th Global AI Hackathon, with the MIT Club of Northern California and the MIT Club of Germany. Powered by Databricks Omnigent. 24-hour scientific discovery challenge. Omnigent is required for every submission.

Source: Agentic Scientific Discovery — Challenge Brief (PDF, since removed from the repo)

## Goal

Build an agentic AI lab for one scientific problem. The moonshot is discovery that is 10× faster. A 10× result is not required in 24 hours. Pick one bottleneck and show a measured improvement (1.5×, 3×, 5×, or 10×). Evidence matters more than the size of the claim.

Inspiration: AlphaFold2 and the 2024 Nobel Prize in Chemistry.

## What to build

In 24 hours, a working lab around one question. Coordinated agents should:

1. Generate insight
2. Formulate hypotheses
3. Design experiments
4. Run computational tests or analyses
5. Interpret results
6. Decide what to investigate next

One complete discovery loop:

**Question → Evidence → Hypothesis → Experiment → Result → Updated decision**

The experiment must produce evidence that changes the next scientific decision. At least two possible tests; choose one using expected learning, feasibility, and cost.

An experiment can be a simulation, computational screen, benchmark, model comparison, analysis of an existing dataset, sensitivity or counterfactual analysis, or a test on data you generate. A wet-lab experiment can be the proposed next step. It is not required during the hackathon.

## Domain

Any science: biology, medicine, materials, energy, climate, agriculture, chemistry, physics, AI research, neuroscience, astronomy, or something else. The question has to be testable in 24 hours with existing datasets, literature, APIs, simulations, models, benchmarks, or data you generate.

## Omnigent (mandatory)

Omnigent orchestrates the live discovery workflow. Show multiple specialist agents exchanging outputs, using tools, and changing the plan after a result.

Two setups:

- **Managed Databricks:** sign in, open `<workspace-url>/omnigent`, New session, Sandbox. A Databricks account is only required for this route.
- **Open source:** install Omnigent from GitHub, run `omnigent`, select a model or agent harness.

For each agent, specify the decision it owns, the tools it can use, its inputs, and its output. Pass structured evidence, candidate IDs, experiment specs, and results between agents. Keep a shared research record so every decision can be reconstructed.

Run independent searches or experiments in parallel. Let surprising results reopen an earlier assumption. Give the planner a budget and make it choose between competing tests.

Humans set the objective and approve consequential actions. A safety agent can flag risks and request approval. Enforce that boundary with tool permissions and Omnigent policies.

Suggested starting sources (not a restriction):

| Area | Sources |
| --- | --- |
| Any field | OpenAlex (papers, citations, communities) |
| Drug discovery and biology | Europe PMC, PubChem |
| Materials and energy | Materials Project, NIST JARVIS |
| AI research | arXiv, OpenML |

Check access keys, licenses, and compute early.

## 24-hour plan from the brief

| First 4 hours | Next 14 hours | Final 6 hours |
| --- | --- | --- |
| Domain, question, measurable outcome, data, tools, and the bottleneck. Confirm you can access the data and that a result can change the next decision. | Omnigent workflow, hypotheses or candidates, experiment selection. One complete loop, including a result that changes the next decision. | Strengthen the experiment, analyze the result, prepare a two-minute demo. |

## What success looks like

- **Omnigent orchestration:** purposeful collaboration between specialist agents.
- **Scientific progress:** an ambitious question and a meaningful, reproducible result.
- **Discovery acceleration:** a real bottleneck, a measured improvement, and a next experiment justified by what the lab learned.
- Citations for factual claims. Attach source evidence or run records. Label agent-generated hypotheses. Preserve uncertainty. Document controls and human approval gates. State the validation still needed before real-world use.

## Scoring

| Weight | Criterion |
| --- | --- |
| 30% | Omnigent orchestration |
| 25% | Breakthrough potential |
| 20% | Discovery acceleration and learning |
| 15% | Scientific rigor |
| 10% | Creativity and responsibility |

## Submission

The repository, agent specifications and policies, a two-minute demo, cited evidence, experiment code and results, the measured improvement, and the next experiment.
