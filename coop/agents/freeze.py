"""Freeze a confirmation config into ``research/record.jsonl``.

Held-out seeds are 1000 and above. A confirmation batch may use them only
when a freeze row matches the detector, the config hash, and those seeds.
Dev seeds 0–999 do not need a freeze row.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from coop.agents.detector import (
    CONTROL_ARM,
    DETECTOR_NAME,
    PREDICTED_DIRECTION,
    detector_id,
)
from coop.agents.prompts import member_system, newcomer_system
from coop.sim.env import git_sha

HELD_OUT_MIN = 1000


def prompt_text() -> str:
    """Live target prompts. The scripted ladder is covered by the sim git sha."""
    return member_system() + "\n\n" + newcomer_system()


def _hash_temperature(temperature: float | None | str) -> float | None:
    """Null when the request omits sampling. A number is a real parameter."""
    if temperature is None:
        return None
    if isinstance(temperature, str):
        if temperature.strip().lower() in {"default", "model_default"}:
            return None
        return float(temperature)
    return float(temperature)


def config_hash(*, model: str, temperature: float | None | str, sim_git_sha: str | None) -> str:
    """Hash of prompts, model id, temperature actually sent, and the sim git sha."""
    body = {
        "model": model,
        "prompts": prompt_text(),
        "sim_git_sha": sim_git_sha,
        "temperature": _hash_temperature(temperature),
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def freeze_record(
    *,
    held_out_seeds: list[int],
    model: str,
    temperature: float | None | str,
    control_arm: str = CONTROL_ARM,
    predicted_direction: str = PREDICTED_DIRECTION,
    sim_git_sha: str | None = None,
    sampling: str | None = None,
) -> dict[str, Any]:
    sha = git_sha() if sim_git_sha is None else sim_git_sha
    sent = _hash_temperature(temperature)
    row: dict[str, Any] = {
        "config_hash": config_hash(model=model, temperature=sent, sim_git_sha=sha),
        "control_arm": control_arm,
        "detector": DETECTOR_NAME,
        "detector_id": detector_id(),
        "held_out_seeds": list(held_out_seeds),
        "model": model,
        "predicted_direction": predicted_direction,
        "same_seeds": True,
        "sim_git_sha": sha,
        "stage": "freeze",
        "temperature": sent,
    }
    if sampling:
        row["sampling"] = sampling
    return row


def append_freeze(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def iter_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def matching_freeze(
    path: Path,
    *,
    seeds: list[int],
    model: str,
    temperature: float | None | str,
) -> dict[str, Any] | None:
    """Return the freeze row that covers every requested seed at or above 1000."""
    needed = {int(seed) for seed in seeds if int(seed) >= HELD_OUT_MIN}
    if not needed:
        return None
    current = config_hash(model=model, temperature=temperature, sim_git_sha=git_sha())
    identity = detector_id()
    for row in iter_records(path):
        if row.get("stage") != "freeze":
            continue
        if row.get("config_hash") != current:
            continue
        if row.get("detector_id") != identity:
            continue
        if not row.get("predicted_direction") or not row.get("control_arm"):
            continue
        held = {int(seed) for seed in row.get("held_out_seeds") or []}
        if needed <= held:
            return row
    return None
