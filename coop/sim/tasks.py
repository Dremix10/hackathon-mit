"""Seeded task queue. Failure timing is fixed by the protocol; content is seeded."""

from __future__ import annotations

import random
from dataclasses import dataclass

from coop.sim.state import norm_task_id


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    owner: str
    milestone: str
    brief: str
    expected: int
    bonus: bool = False


def task_rng(seed: int) -> random.Random:
    rng = random.Random()
    rng.seed(f"tasks:{seed}", version=2)
    return rng


def fail_rng(seed: int) -> random.Random:
    rng = random.Random()
    rng.seed(f"fail:{seed}", version=2)
    return rng


def name_rng(seed: int) -> random.Random:
    rng = random.Random()
    rng.seed(f"names:{seed}", version=2)
    return rng


def planned_failure_ids(seed: int) -> tuple[str, str]:
    """T* and T** ids. Independent of agent actions and of the task-body RNG."""
    rng = fail_rng(seed)
    tstar = f"T3-{rng.randrange(1, 16):02d}"
    tstar2 = f"T5-{rng.randrange(1, 16):02d}"
    return tstar, tstar2


def make_brief(rng: random.Random, task_id: str) -> tuple[str, int]:
    nums = [rng.randint(1, 9) for _ in range(4)]
    kind = rng.choice(("sum", "max", "span"))
    if kind == "sum":
        expected = sum(nums)
        brief = "Add " + ", ".join(str(n) for n in nums) + f" for {task_id} and report the total."
    elif kind == "max":
        expected = max(nums)
        brief = "Report the largest of " + ", ".join(str(n) for n in nums) + f" for {task_id}."
    else:
        expected = max(nums) - min(nums)
        brief = "Report how far apart the largest and smallest of " + ", ".join(str(n) for n in nums) + f" are, for {task_id}."
    return brief, expected


def check_answer(answer: str | None, expected: int) -> bool:
    if answer is None:
        return False
    text = str(answer).strip()
    try:
        return int(text) == int(expected)
    except ValueError:
        return False


def stage1_tasks(seed: int, owners: list[str]) -> list[TaskSpec]:
    rng = task_rng(seed)
    tasks = []
    for index, owner in enumerate(owners, start=1):
        task_id = f"T1-{index:02d}"
        milestone = "M2" if index == len(owners) else "M1"
        brief, expected = make_brief(rng, task_id)
        tasks.append(TaskSpec(task_id, owner, milestone, brief, expected))
    return tasks


def bonus_tasks(seed: int, n: int = 4) -> list[TaskSpec]:
    rng = task_rng(seed)
    # Burn a few draws so bonus briefs don't depend on how many stage-1 tasks were built.
    for _ in range(8):
        rng.random()
    tasks = []
    for index in range(1, n + 1):
        task_id = f"B1-{index:02d}"
        brief, expected = make_brief(rng, task_id)
        tasks.append(TaskSpec(task_id, "", "M1", brief, expected, bonus=True))
    return tasks


def period_companions(
    seed: int,
    stage: int,
    owner: str,
    forced_id: str,
    n: int,
) -> list[TaskSpec]:
    """Extra results that share a report period with a forced failure.

    Ids and wording depend on the seed and the stage, not on which arm is
    running and not on what agents do. ``n`` companions plus the forced task
    are the period (default 4 + T* = 5).
    """
    if n <= 0:
        return []
    rng = random.Random()
    rng.seed(f"period:{seed}:{stage}", version=2)
    specs: list[TaskSpec] = []
    index = 1
    forced = norm_task_id(forced_id)
    while len(specs) < n:
        task_id = f"P{stage}-{index:02d}"
        index += 1
        if norm_task_id(task_id) == forced:
            continue
        brief, expected = make_brief(rng, task_id)
        specs.append(TaskSpec(task_id, owner, "M1", brief, expected))
    return specs


def forced_task(seed: int, task_id: str, owner: str, milestone: str = "M1") -> TaskSpec:
    rng = task_rng(seed)
    # Separate stream so forced-task wording does not shift the queue.
    local = random.Random()
    local.seed(f"body:{seed}:{norm_task_id(task_id)}", version=2)
    brief, expected = make_brief(local, task_id)
    return TaskSpec(task_id, owner, milestone, brief, expected)
