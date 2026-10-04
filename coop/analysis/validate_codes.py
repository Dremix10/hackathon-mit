"""Compare blind message codes to a hand-labeled CSV.

The coder and the LLM judge see ``message_id`` and ``text`` only.
Cohen's kappa and percent agreement are printed and returned.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from coop.analysis.coding import code_messages
from coop.analysis.llm import CODING_SYSTEM, MockLLM, make_client


def cohens_kappa(left: list[str], right: list[str]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("kappa needs two equal non-empty label lists")
    labels = sorted(set(left) | set(right))
    index = {label: i for i, label in enumerate(labels)}
    size = len(labels)
    matrix = [[0 for _ in range(size)] for _ in range(size)]
    for a, b in zip(left, right):
        matrix[index[a]][index[b]] += 1
    n = len(left)
    observed = sum(matrix[i][i] for i in range(size)) / n
    row_sums = [sum(row) for row in matrix]
    col_sums = [sum(matrix[i][j] for i in range(size)) for j in range(size)]
    expected = sum(row_sums[i] * col_sums[i] for i in range(size)) / (n * n)
    if expected == 1.0:
        return 1.0
    return (observed - expected) / (1.0 - expected)


def percent_agreement(left: list[str], right: list[str]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("agreement needs two equal non-empty label lists")
    return sum(a == b for a, b in zip(left, right)) / len(left)


def read_hand_labels(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"message_id", "text", "hand_label"}
        if not reader.fieldnames or not required <= set(reader.fieldnames):
            raise ValueError(f"{path}: need columns {sorted(required)}")
        rows = []
        for row in reader:
            rows.append(
                {
                    "message_id": row["message_id"],
                    "text": row["text"],
                    "hand_label": row["hand_label"],
                    "notes": row.get("notes") or "",
                }
            )
    if len(rows) < 30:
        raise ValueError(f"{path}: expected about 30 labeled messages, found {len(rows)}")
    return rows


def _llm_labels(rows: list[dict[str, str]], llm: MockLLM) -> list[str]:
    lines = [f"MESSAGE id={row['message_id']} text={row['text']}" for row in rows]
    raw = llm.complete(CODING_SYSTEM, "\n".join(lines))
    parsed = json.loads(raw)
    if parsed.get("model_generated") is not True:
        raise ValueError("message-coding output must be marked model-generated")
    by_id = {item["message_id"]: item["label"] for item in parsed["labels"]}
    return [by_id[row["message_id"]] for row in rows]


def validate_label_file(path: Path, llm=None) -> dict:
    """Score the blind coder against hand labels.

    ``hand_label`` is not passed into the coder. Condition columns are not read.
    """
    rows = read_hand_labels(path)
    blinded = [{"message_id": row["message_id"], "text": row["text"]} for row in rows]
    rule = [item["label"] for item in code_messages(blinded)]
    model = llm or MockLLM()
    judged = _llm_labels(rows, model)
    hand = [row["hand_label"] for row in rows]
    return {
        "n": len(rows),
        "blind": True,
        "inputs": ["message_id", "text"],
        "rule_percent_agreement": percent_agreement(hand, rule),
        "rule_cohens_kappa": cohens_kappa(hand, rule),
        "llm_percent_agreement": percent_agreement(hand, judged),
        "llm_cohens_kappa": cohens_kappa(hand, judged),
        "llm_model_generated": True,
        "llm_model": getattr(model, "name", "unknown"),
        "note": (
            "Hand labels are a coding check, not simulator ground truth. "
            "The judge did not see condition, mode, or sealed labels."
        ),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Cohen's kappa for blind message codes")
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--llm", choices=("mock", "anthropic"), default="mock")
    args = parser.parse_args(argv)
    result = validate_label_file(args.labels, make_client(args.llm))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
