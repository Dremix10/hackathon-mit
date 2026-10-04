"""Real-run schedule. The pilot is not confirmatory.

Pilot: 10 dev runs, earned access, deliverable-only visibility, scripted
insider, one model and temperature. Split: one run of each recruiter
objective, three null, four pressure-only.

Main batch: held-out seeds, three equal groups. The recruiter group cycles
the three objectives. This is not a five-arm Latin square.

``n_per_group`` stays unknown until the pilot's actual spend is logged::

    floor((100 - pilot_spend - 10) / (3 * measured_usd_per_run))
"""

from __future__ import annotations

import math

from coop.sim.seeds import load_registry, used_seeds

# Distinct dev-pool seeds. Examples already used 1–3.
PILOT_PAIRS: tuple[tuple[str, int], ...] = (
    ("resource_capture", 4),
    ("blame_avoidance", 5),
    ("loyalty", 6),
    ("null", 7),
    ("null", 8),
    ("null", 9),
    ("pressure_only", 10),
    ("pressure_only", 11),
    ("pressure_only", 12),
    ("pressure_only", 13),
)

RECRUITER_OBJECTIVES: tuple[str, ...] = (
    "resource_capture",
    "blame_avoidance",
    "loyalty",
)

TEAM_CAP_USD = 100.0
RESERVE_USD = 10.0
HELD_OUT_MIN = 1000


def pilot_counts() -> dict[str, int]:
    counts = {"recruiter": 0, "null": 0, "pressure_only": 0}
    for arm, _seed in PILOT_PAIRS:
        if arm == "pressure_only":
            counts["pressure_only"] += 1
        elif arm == "null":
            counts["null"] += 1
        else:
            counts["recruiter"] += 1
    return counts


def main_pairs(seeds: list[int]) -> list[tuple[str, int]]:
    """Three runs per seed: one recruiter objective, null, pressure-only."""
    if not seeds:
        raise ValueError("main batch needs at least one held-out seed")
    pairs: list[tuple[str, int]] = []
    for index, seed in enumerate(seeds):
        seed = int(seed)
        if seed < HELD_OUT_MIN:
            raise ValueError(f"main-batch seed {seed} is below {HELD_OUT_MIN}")
        pairs.append((RECRUITER_OBJECTIVES[index % len(RECRUITER_OBJECTIVES)], seed))
        pairs.append(("null", seed))
        pairs.append(("pressure_only", seed))
    return pairs


def n_per_group(pilot_spend: float, usd_per_run: float) -> int:
    """How many held-out seeds each of the three groups can afford.

    Returns 0 when the remaining room cannot buy one seed of each group.
    Raises ValueError when the measured rate is missing or not positive.
    """
    spend = float(pilot_spend)
    rate = float(usd_per_run)
    if spend < 0 or rate <= 0:
        raise ValueError("measured $/run must be positive and pilot spend must be known")
    room = TEAM_CAP_USD - spend - RESERVE_USD
    if room <= 0:
        return 0
    return int(math.floor(room / (3.0 * rate)))


def held_out_pool() -> list[int]:
    return list(load_registry().pools["held_out"])


def confirmation_seeds(n: int, log_path: str = "research/seed_log.jsonl") -> list[int]:
    """First ``n`` unused seeds in the held-out draw pool."""
    from pathlib import Path

    if n < 1:
        return []
    pool = held_out_pool()
    used = used_seeds(Path(log_path), "held_out")
    fresh = [seed for seed in pool if seed not in used]
    return fresh[:n]
