"""Manipulation checks (protocol §6). These gate confirmatory output.

Blind checks use events and meta only. The insider step-sequence check reads
``sealed.json`` ``template_map`` and runs only after unblinding.
"""

from __future__ import annotations

from coop.eval.lint import lint_events
from coop.eval.outcomes import identify_tstar, identify_tstar_star

_SCRIPT_TYPES = frozenset({"message", "invite", "vote", "doc_edit", "credit_transfer"})


def no_conflict_flag(meta: dict) -> bool:
    """Canonical field is boolean ``no_conflict``.

    Also accepted: ``mode == "no_conflict"``. That string is not a protocol
    mode. It is tolerated so a writer that parked the flag on ``mode`` still
    loads. Boolean true wins if both are set.
    """
    if meta.get("no_conflict") is True:
        return True
    return meta.get("mode") == "no_conflict"


def _in_scope(meta: dict) -> bool:
    """T* / T** are required everywhere except the no-failure control."""
    return not no_conflict_flag(meta)


def blind_manipulation_problems(runs: list) -> list[str]:
    """Checks that do not read sealed.json. One failure blocks confirmatory output."""
    problems: list[str] = []
    scoped = [run for run in runs if run.ok and _in_scope(run.meta)]
    if not scoped:
        problems.append("no in-scope runs for the T*/T** failure check")
        return problems
    missing_star = []
    missing_star2 = []
    for run in scoped:
        if identify_tstar(run.events, run.meta) is None:
            missing_star.append(run.run_id)
        if identify_tstar_star(run.events, run.meta) is None:
            missing_star2.append(run.run_id)
        insider = run.meta.get("insider_id")
        if isinstance(insider, str):
            for event in run.events:
                if event.get("type") == "violation" and event.get("actor") == insider:
                    problems.append(f"{run.run_id}: violation actor is insider_id {insider}")
                    break
        model_problem = _model_problem(run)
        if model_problem:
            problems.append(model_problem)
        for hit in lint_events(run.events):
            problems.append(f"{run.run_id}: {hit}")
    if missing_star:
        problems.append("T* did not fail in: " + ", ".join(missing_star))
    if missing_star2:
        problems.append("T** did not fail in: " + ", ".join(missing_star2))
    return problems


def _model_problem(run) -> str | None:
    """Non-insider agents share one model id and one temperature."""
    insider = run.meta.get("insider_id")
    seen: list[tuple[str, object]] = []
    for agent in run.meta.get("agents") or []:
        if not isinstance(agent, dict):
            continue
        if isinstance(insider, str) and agent.get("id") == insider:
            continue
        seen.append((str(agent.get("model")), agent.get("temperature")))
    if len({item for item in seen}) > 1:
        return f"{run.run_id}: non-insider model/temperature values differ: {seen}"
    return None


def step_sequence_problems(events: list[dict], meta: dict, sealed: dict | None) -> list[str]:
    """Insider template_id sequence equals the map, given logged branches.

    A step with one template is required. A step with several templates must
    emit exactly the template whose ``branch`` equals ``payload.branch``.
    Map order is the expected order. Not applicable when there is no insider.
    """
    insider = meta.get("insider_id")
    if not isinstance(insider, str):
        return []
    if not isinstance(sealed, dict):
        return [f"{meta.get('run_id')}: controlled run is missing sealed.json for the step check"]
    template_map = sealed.get("template_map")
    if not isinstance(template_map, dict) or not template_map:
        return [f"{meta.get('run_id')}: template_map is missing"]
    problems: list[str] = []
    emitted: list[tuple[str, dict]] = []
    for event in events:
        if event.get("actor") != insider or event.get("type") not in _SCRIPT_TYPES:
            continue
        template_id = (event.get("payload") or {}).get("template_id")
        if not isinstance(template_id, str):
            continue
        if template_id not in template_map:
            problems.append(f"{meta.get('run_id')}: template_id {template_id} is not in template_map")
            continue
        entry = template_map[template_id]
        if isinstance(entry, dict) and "branch" in entry:
            if entry.get("branch") != (event.get("payload") or {}).get("branch"):
                problems.append(
                    f"{meta.get('run_id')}: step {entry.get('step')} branch does not match the log"
                )
        emitted.append((template_id, event))

    options: dict[str, list[tuple[str, dict]]] = {}
    order: list[str] = []
    for template_id, entry in template_map.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("step"), str):
            problems.append(f"{meta.get('run_id')}: template_map[{template_id}] needs a step id")
            continue
        step = entry["step"]
        if step not in options:
            order.append(step)
            options[step] = []
        options[step].append((template_id, entry))

    logged_branch: dict[str, object] = {}
    for template_id, event in emitted:
        entry = template_map.get(template_id)
        if isinstance(entry, dict) and isinstance(entry.get("step"), str):
            logged_branch[entry["step"]] = (event.get("payload") or {}).get("branch")

    expected: list[str] = []
    for step in order:
        choices = options[step]
        if len(choices) == 1:
            expected.append(choices[0][0])
            continue
        branch = logged_branch.get(step)
        matched = [template_id for template_id, entry in choices if entry.get("branch") == branch]
        if len(matched) != 1:
            problems.append(
                f"{meta.get('run_id')}: step {step} logged branch {branch!r} did not select one template"
            )
            continue
        expected.append(matched[0])

    got = [template_id for template_id, _event in emitted]
    if got != expected:
        problems.append(f"{meta.get('run_id')}: insider steps {got} != expected {expected}")
    return problems
