"""Load ``events.jsonl`` and ``meta.json``.

``sealed.json`` is not read by the classifier. ``load_run(..., with_sealed=True)``
is the unblinding path: it checks ``sealed_sha256`` against the file bytes
and then parses the objective.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coop.analysis.types import CHANNELS, STAGES, Event
from coop.schema import DRIVERS, EVENT_TYPES, MIN_ROUNDS_CONTROLLED, MODES, OBJECTIVES


class ContractError(ValueError):
    """A run file does not match the shared event contract."""


def scoring_events(events: list[Any]) -> list[Any]:
    """Drop ``driver_error`` turns. Infrastructure failures are not behavior."""
    kept = []
    for event in events:
        kind = event.get("type") if isinstance(event, dict) else getattr(event, "type", None)
        if kind == "driver_error":
            continue
        kept.append(event)
    return kept


def load_events(path: Path) -> list[Event]:
    if not path.is_file():
        raise ContractError(f"missing events file: {path}")
    events: list[Event] = []
    text = path.read_text(encoding="utf-8")
    for line_no, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContractError(f"{path}:{line_no + 1}: invalid JSON") from exc
        events.append(_event_from_obj(raw, path, line_no))
    events.sort(key=lambda e: (e.t, e.event_id))
    return events


def _event_from_obj(raw: dict[str, Any], path: Path, line_no: int) -> Event:
    required = ("run_id", "t", "stage", "type", "channel", "payload")
    missing = [key for key in required if key not in raw]
    if missing:
        raise ContractError(f"{path}:{line_no + 1}: missing {missing}")
    if raw["channel"] not in CHANNELS:
        raise ContractError(f"{path}:{line_no + 1}: bad channel {raw['channel']!r}")
    if raw["type"] not in EVENT_TYPES:
        raise ContractError(f"{path}:{line_no + 1}: bad type {raw['type']!r}")
    stage = raw["stage"]
    if stage not in STAGES:
        raise ContractError(f"{path}:{line_no + 1}: stage {stage} not in 1..5")
    if not isinstance(raw["payload"], dict):
        raise ContractError(f"{path}:{line_no + 1}: payload must be an object")
    gt = raw.get("ground_truth")
    if gt is not None and not isinstance(gt, dict):
        raise ContractError(f"{path}:{line_no + 1}: ground_truth must be an object or null")
    run_id = str(raw["run_id"])
    return Event(
        run_id=run_id,
        t=int(raw["t"]),
        stage=int(stage),
        type=str(raw["type"]),
        actor=raw.get("actor"),
        target=raw.get("target"),
        channel=str(raw["channel"]),
        payload=dict(raw["payload"]),
        ground_truth=dict(gt) if gt else None,
        event_id=f"{run_id}:{line_no:04d}",
    )


def load_meta(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ContractError(f"missing meta file: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ContractError(f"{path}: meta must be an object")
    for key in ("run_id", "condition", "mode", "seed", "agents"):
        if key not in raw:
            raise ContractError(f"{path}: missing {key}")
    if "recruiter_objective" in raw or "objective" in raw:
        raise ContractError(
            f"{path}: meta must not carry the arm or objective; that belongs in sealed.json"
        )
    mode = raw["mode"]
    if mode not in MODES:
        raise ContractError(f"{path}: mode must be one of {MODES}")
    insider = raw.get("insider_id", None)
    if "insider_id" not in raw:
        raise ContractError(f"{path}: missing insider_id")
    driver = raw.get("insider_driver")
    if driver is not None and driver not in DRIVERS:
        raise ContractError(f"{path}: insider_driver must be one of {DRIVERS}")
    if mode == "controlled":
        if not insider:
            raise ContractError(
                f"{path}: controlled mode requires a non-null insider_id, including the null arm"
            )
        digest = raw.get("sealed_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ContractError(f"{path}: controlled mode requires sealed_sha256")
        rounds = raw.get("rounds_per_stage")
        if rounds is not None:
            if len(rounds) != 5 or any(
                int(have) < int(need) for have, need in zip(rounds, MIN_ROUNDS_CONTROLLED)
            ):
                raise ContractError(
                    f"{path}: controlled mode requires rounds_per_stage >= [4, 4, 5, 4, 4]"
                )
    else:
        if insider is not None:
            raise ContractError(f"{path}: {mode} mode requires insider_id null")
        if raw.get("sealed_sha256"):
            raise ContractError(f"{path}: {mode} mode must not carry sealed_sha256")
    return raw


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_sealed(directory: Path, meta: dict[str, Any]) -> dict[str, Any] | None:
    """Open ``sealed.json`` and check the hash. Controlled runs only.

    Call this only after blind predictions have been written.
    """
    path = directory / "sealed.json"
    mode = meta.get("mode")
    if mode != "controlled":
        if path.is_file():
            raise ContractError(f"{directory.name}: {mode} run must not have sealed.json")
        return None
    if not path.is_file():
        raise ContractError(f"{directory.name}: controlled run is missing sealed.json")
    digest = file_sha256(path)
    expected = meta.get("sealed_sha256")
    if digest != expected:
        raise ContractError(
            f"{directory.name}: sealed_sha256 mismatch (file {digest}, meta {expected})"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("recruiter_objective") not in OBJECTIVES:
        raise ContractError(f"{path}: recruiter_objective must be one of {OBJECTIVES}")
    driver = raw.get("insider_driver", meta.get("insider_driver"))
    if driver is not None and driver not in DRIVERS:
        raise ContractError(f"{path}: insider_driver must be one of {DRIVERS}")
    return raw


@dataclass
class Run:
    """One run directory. ``sealed`` stays None until an explicit unblind load."""

    run_id: str
    directory: Path
    events: list[Event]
    meta: dict[str, Any]
    sealed: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)


def load_run(directory: Path, *, with_sealed: bool = False) -> Run:
    events = load_events(directory / "events.jsonl")
    meta = load_meta(directory / "meta.json")
    sealed = read_sealed(directory, meta) if with_sealed else None
    return Run(
        run_id=str(meta["run_id"]),
        directory=directory,
        events=events,
        meta=meta,
        sealed=sealed,
        warnings=[],
    )


_MOCK_PROFILES = ("comply_all", "refuse_all", "partial", "adversarial")


def parse_mock_stem(stem: str) -> tuple[str, str]:
    """``mock_<arm>_<profile>`` -> ``(arm, profile)``."""
    body = stem.removeprefix("mock_")
    profile = next((item for item in _MOCK_PROFILES if body.endswith("_" + item)), None)
    if profile is None:
        raise ContractError(f"unrecognized golden mock name: {stem}")
    arm = body[: -(len(profile) + 1)]
    return arm, profile


def load_golden(path: Path, *, with_sealed: bool = False) -> Run:
    """Load one ``mock_<arm>_<profile>.jsonl`` log.

    The arm is in the filename because that is how the simulator published
    the golden logs. It is attached to ``sealed`` only when ``with_sealed``
    is true, after blind predictions have been logged. The classifier's meta
    does not carry it.
    """
    stem = path.stem
    arm, profile = parse_mock_stem(stem)
    events = load_events(path)
    for event in events:
        suffix = event.event_id.split(":")[-1]
        event.run_id = stem
        event.event_id = f"{stem}:{suffix}"
    if arm == "pressure_only":
        mode = "pressure_only"
        insider = None
        objective = None
    else:
        mode = "controlled"
        insider = "a0"
        objective = arm
    meta: dict[str, Any] = {
        "run_id": stem,
        "mode": mode,
        "insider_id": insider,
        "insider_driver": "scripted",
        "seed": 1,
        "condition": {"access": "earned", "visibility": "deliverable_only"},
        "agents": [{"id": agent_id} for agent_id in ("a0", "a1", "a2", "a3", "a4")],
        "source": "golden_mock",
        "synthetic": False,
        "behavior_profile": profile,
        "protocol_version": "1.1",
    }
    sealed = None
    if with_sealed and objective is not None:
        sealed = {
            "recruiter_objective": objective,
            "insider_driver": "scripted",
            "label_source": "filename",
        }
    return Run(
        run_id=stem,
        directory=path,
        events=events,
        meta=meta,
        sealed=sealed,
        warnings=["golden mock log; arm label is the filename, not a sealed.json file"],
    )


def list_run_paths(root: Path) -> list[Path]:
    if not root.is_dir():
        raise ContractError(f"runs path is not a directory: {root}")
    dirs = [
        path for path in root.iterdir() if path.is_dir() and (path / "events.jsonl").is_file()
    ]
    mocks = [
        path
        for path in root.iterdir()
        if path.is_file() and path.name.startswith("mock_") and path.suffix == ".jsonl"
    ]
    paths = sorted(dirs) + sorted(mocks)
    if not paths:
        raise ContractError(f"no runs under {root}")
    return paths


def discover_runs(root: Path, *, with_sealed: bool = False) -> list[Run]:
    runs: list[Run] = []
    for path in list_run_paths(root):
        if path.is_dir():
            runs.append(load_run(path, with_sealed=with_sealed))
        else:
            runs.append(load_golden(path, with_sealed=with_sealed))
    return runs
