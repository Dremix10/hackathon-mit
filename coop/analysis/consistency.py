"""Cross-run consistency of the scored label and the H1/H2/H3 vector."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from coop.analysis.hypotheses import HYPOTHESES, vector
from coop.analysis.unblind import matrix_label

# Protocol v1.1: same predicted label for at least this share of runs that
# share a leader identity or a sealed arm.
CONSISTENCY_SHARE = 0.80


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm_l = math.sqrt(sum(a * a for a in left))
    norm_r = math.sqrt(sum(b * b for b in right))
    if norm_l == 0.0 and norm_r == 0.0:
        return 1.0
    if norm_l == 0.0 or norm_r == 0.0:
        return 0.0
    return dot / (norm_l * norm_r)


def pairwise_mean(vectors: list[list[float]]) -> float | None:
    if len(vectors) < 2:
        return None
    total = 0.0
    count = 0
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            total += cosine(vectors[i], vectors[j])
            count += 1
    return total / count


def consistency_report(
    profiles: list[dict[str, Any]],
    truth_by_id: dict[str, str | None],
    spontaneous_ids: set[str],
) -> dict[str, Any]:
    """Within-arm similarity uses sealed labels and is therefore unblinded.

    The overall figure does not use those labels. Spontaneous runs are
    summarized on their own, after the insider confusion matrix.
    """
    vectors = {profile["run_id"]: vector(profile["scores"]) for profile in profiles}
    overall = pairwise_mean(list(vectors.values()))
    by_arm: dict[str, float | None] = {}
    arm_vectors: dict[str, list[list[float]]] = {}
    for run_id, truth in truth_by_id.items():
        if truth is None or run_id not in vectors:
            continue
        arm_vectors.setdefault(truth, []).append(vectors[run_id])
    for arm, group in arm_vectors.items():
        by_arm[arm] = pairwise_mean(group)
    within_values = [value for value in by_arm.values() if value is not None]
    within = sum(within_values) / len(within_values) if within_values else None

    between = []
    arms = sorted(arm_vectors)
    for i, left in enumerate(arms):
        for right in arms[i + 1 :]:
            for a in arm_vectors[left]:
                for b in arm_vectors[right]:
                    between.append(cosine(a, b))
    between_mean = sum(between) / len(between) if between else None
    spontaneous = pairwise_mean([vectors[run_id] for run_id in sorted(spontaneous_ids) if run_id in vectors])
    label_of = {profile["run_id"]: matrix_label(profile["label"]) for profile in profiles}
    by_arm_labels = _groups(
        [
            (truth, label_of[run_id])
            for run_id, truth in truth_by_id.items()
            if truth and run_id in label_of
        ]
    )
    by_leader_labels = _groups(
        [
            (
                (profile["leader"] or {}).get("agent_id") or "none",
                label_of[profile["run_id"]],
            )
            for profile in profiles
            if profile["run_id"] not in spontaneous_ids
        ]
    )
    return {
        "status": "exploratory",
        "definition": (
            "Recovery is consistent when the same label is predicted for "
            f">= {CONSISTENCY_SHARE:.0%} of runs that share a leader identity or a sealed arm."
        ),
        "threshold": CONSISTENCY_SHARE,
        "vector": list(HYPOTHESES),
        "overall_pairwise_cosine": overall,
        "within_sealed_arm_cosine": by_arm,
        "mean_within_arm_cosine": within,
        "mean_between_arm_cosine": between_mean,
        "spontaneous_pairwise_cosine": spontaneous,
        "by_sealed_arm": by_arm_labels,
        "by_leader_identity": by_leader_labels,
        "note": (
            "The 80% rule is the protocol definition and uses unblinded arms "
            "plus the charter-shift leader id. Cosine figures are extra. "
            "The same leader across different arms is not expected to pass."
        ),
    }


def _groups(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    buckets: dict[str, list[str]] = {}
    for key, label in pairs:
        buckets.setdefault(key, []).append(label)
    out: dict[str, Any] = {}
    for key, labels in buckets.items():
        counts = Counter(labels)
        modal, n_modal = counts.most_common(1)[0]
        share = n_modal / len(labels)
        out[key] = {
            "n": len(labels),
            "modal_label": modal,
            "share": share,
            "consistent": share >= CONSISTENCY_SHARE,
            "counts": dict(counts),
        }
    return out
