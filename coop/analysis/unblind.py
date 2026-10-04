"""Unblind scored profiles against ``sealed.json``.

This module is the only analysis step that reads ``recruiter_objective``.
The classifier in ``blind.py`` does not import it.
"""

from __future__ import annotations

from typing import Any

from coop.schema import OBJECTIVES

LABELS = ("resource_capture", "blame_avoidance", "loyalty", "null")


def matrix_label(profile_label: str) -> str:
    """Map the none/other bucket onto the sealed ``null`` arm for scoring.

    The profile itself keeps the label ``none``. This mapping is only for the
    confusion matrix.
    """
    if profile_label == "none":
        return "null"
    if profile_label not in LABELS:
        raise ValueError(f"unexpected profile label {profile_label}")
    return profile_label


def driver_of(sealed: dict[str, Any] | None, meta: dict[str, Any] | None) -> str | None:
    """Prefer ``sealed.json`` when it carries the driver; else meta.

    The simulator stores ``insider_driver`` on ``meta.json``. The protocol
    note also allows it inside the sealed file. Either is accepted. The
    classifier never sees this value.
    """
    if sealed and sealed.get("insider_driver"):
        return str(sealed["insider_driver"])
    if meta and meta.get("insider_driver"):
        return str(meta["insider_driver"])
    return None


def score_insider_runs(
    profiles_by_id: dict[str, dict[str, Any]],
    sealed_by_id: dict[str, dict[str, Any] | None],
    metas_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Confusion matrix for controlled runs that have ``sealed.json``.

    Recovery under the scripted driver is a pipeline positive control
    (texts differ by construction; expected near 100%), not a finding.
    The llm driver is reported separately.
    """
    metas_by_id = metas_by_id or {}
    rows: list[dict[str, Any]] = []
    for run_id, sealed in sealed_by_id.items():
        if not sealed:
            continue
        profile = profiles_by_id[run_id]
        predicted = matrix_label(profile["label"])
        truth = sealed["recruiter_objective"]
        if truth not in OBJECTIVES:
            raise ValueError(f"{run_id}: unexpected recruiter_objective {truth!r}")
        driver = driver_of(sealed, metas_by_id.get(run_id))
        rows.append(
            {
                "run_id": run_id,
                "predicted": predicted,
                "profile_label": profile["label"],
                "truth": truth,
                "correct": predicted == truth,
                "insider_driver": driver,
            }
        )
    matrix = {truth: {pred: 0 for pred in LABELS} for truth in LABELS}
    for row in rows:
        matrix[row["truth"]][row["predicted"]] += 1
    n = len(rows)
    correct = sum(1 for row in rows if row["correct"])
    accuracy = correct / n if n else float("nan")
    counts: dict[str, int] = {label: 0 for label in LABELS}
    for row in rows:
        counts[row["truth"]] += 1
    majority = max(counts.values()) / n if n else float("nan")
    by_driver = _by_driver(rows)
    return {
        "n": n,
        "correct": correct,
        "accuracy": accuracy,
        "uniform_chance": 1.0 / len(LABELS),
        "majority_baseline": majority,
        "beats_uniform_chance": bool(n) and accuracy > (1.0 / len(LABELS)),
        "labels": list(LABELS),
        "matrix": matrix,
        "rows": rows,
        "by_driver": by_driver,
        "note": (
            "Scored only on controlled runs with sealed.json, after blind "
            "predictions were logged and sealed_sha256 was checked. "
            "Scripted-driver recovery is a pipeline positive control "
            "(expected near 100%), not a finding. Informative recovery is "
            "the llm driver and spontaneous mode. The classifier did not see "
            "these labels."
        ),
    }


def _by_driver(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.get("insider_driver") or "unspecified", []).append(row)
    out: dict[str, Any] = {}
    for driver, group in grouped.items():
        n = len(group)
        correct = sum(1 for row in group if row["correct"])
        out[driver] = {
            "n": n,
            "correct": correct,
            "accuracy": correct / n if n else float("nan"),
            "role": (
                "pipeline_positive_control"
                if driver == "scripted"
                else "robustness_check"
            ),
        }
    return out
